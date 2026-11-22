"""Thread-based job queue with progress, pause/resume and cancellation.

Jobs are executed by a fixed pool of worker threads (``max_concurrent_jobs``).
All state changes go through a per-job lock, so HTTP handler threads can read
status while workers update it.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from ai_engine.core.task_manager import TaskManager
from ai_engine.processors.base import JobCancelled, JobContext
from shared.constants.engine_constants import ACTIVE_JOB_STATES, JobState, TERMINAL_JOB_STATES, ErrorCode
from shared.contracts.errors import ErrorInfo
from shared.contracts.job_contracts import JobRequest, JobStatus

log = logging.getLogger("ai_engine.jobs")


class JobNotFound(KeyError):
    pass


class InvalidJobState(RuntimeError):
    def __init__(self, job_id: str, state: JobState, action: str) -> None:
        super().__init__("cannot %s job %s in state '%s'" % (action, job_id, state.value))
        self.job_id = job_id
        self.state = state
        self.action = action


class QueueFull(RuntimeError):
    pass


class QueueClosed(RuntimeError):
    pass


class Job:
    """Internal mutable job record. Use :meth:`snapshot` to get an immutable contract object."""

    def __init__(self, request: JobRequest, params: Dict[str, Any]) -> None:
        self.job_id = uuid.uuid4().hex
        self.request = request
        self.params = params
        self.lock = threading.Lock()
        self.state = JobState.QUEUED
        self.progress = 0.0
        self.message = "Queued"
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[ErrorInfo] = None
        self.cancel_event = threading.Event()
        self.resume_event = threading.Event()
        self.resume_event.set()

    def snapshot(self) -> JobStatus:
        with self.lock:
            return JobStatus(job_id=self.job_id, job_type=self.request.job_type, state=self.state,
                             progress=self.progress, message=self.message, label=self.request.label,
                             created_at=self.created_at, started_at=self.started_at,
                             finished_at=self.finished_at, cancel_requested=self.cancel_event.is_set(),
                             result=self.result, error=self.error)


class JobQueue:
    def __init__(self, task_manager: TaskManager, max_concurrent_jobs: int = 2, max_queued_jobs: int = 100,
                 history_limit: int = 200) -> None:
        self._tasks = task_manager
        self._max_queued = max_queued_jobs
        self._history_limit = history_limit
        self._jobs: "collections.OrderedDict[str, Job]" = collections.OrderedDict()
        self._lock = threading.Lock()
        self._pending: "queue.Queue[Optional[Job]]" = queue.Queue()
        self._closed = False
        self._workers = [threading.Thread(target=self._worker_loop, name="mait-worker-%d" % i, daemon=True)
                         for i in range(max_concurrent_jobs)]
        for worker in self._workers:
            worker.start()

    # ------------------------------------------------------------------ public API
    def submit(self, request: JobRequest) -> JobStatus:
        """Validate and queue a job. Raises UnknownJobType, ContractError, QueueFull or QueueClosed."""
        processor = self._tasks.get(request.job_type)
        params = processor.validate_params(dict(request.params))
        job = Job(request, params)
        with self._lock:
            if self._closed:
                raise QueueClosed("engine is shutting down")
            queued = sum(1 for j in self._jobs.values() if j.state == JobState.QUEUED)
            if queued >= self._max_queued:
                raise QueueFull("queue is full (%d queued jobs)" % queued)
            self._jobs[job.job_id] = job
            self._prune_history_locked()
        self._pending.put(job)
        log.info("Job submitted", extra={"job_id": job.job_id, "job_type": request.job_type})
        return job.snapshot()

    def get(self, job_id: str) -> JobStatus:
        return self._job(job_id).snapshot()

    def list(self) -> List[JobStatus]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j.snapshot() for j in jobs]

    def cancel(self, job_id: str) -> JobStatus:
        job = self._job(job_id)
        with job.lock:
            if job.state in (JobState.CANCELLED, JobState.CANCELLING):
                pass  # idempotent
            elif job.state in TERMINAL_JOB_STATES:
                raise InvalidJobState(job_id, job.state, "cancel")
            elif job.state == JobState.QUEUED:
                job.cancel_event.set()
                job.state = JobState.CANCELLED
                job.message = "Cancelled before start"
                job.finished_at = time.time()
            else:
                job.cancel_event.set()
                job.resume_event.set()  # wake a paused job so it can observe the cancel
                job.state = JobState.CANCELLING
                job.message = "Cancellation requested"
        log.info("Job cancel requested", extra={"job_id": job_id})
        return job.snapshot()

    def pause(self, job_id: str) -> JobStatus:
        job = self._job(job_id)
        with job.lock:
            if job.state == JobState.PAUSED:
                return self._snapshot_unlocked(job)
            if job.state != JobState.RUNNING:
                raise InvalidJobState(job_id, job.state, "pause")
            job.resume_event.clear()
            job.message = "Pause requested"
        return job.snapshot()

    def resume(self, job_id: str) -> JobStatus:
        job = self._job(job_id)
        with job.lock:
            if job.state not in (JobState.PAUSED, JobState.RUNNING):
                raise InvalidJobState(job_id, job.state, "resume")
            job.resume_event.set()
            if job.state == JobState.PAUSED:
                job.state = JobState.RUNNING
                job.message = "Resumed"
        return job.snapshot()

    def counts(self) -> Dict[str, int]:
        with self._lock:
            jobs = list(self._jobs.values())
        active = sum(1 for j in jobs if j.state in ACTIVE_JOB_STATES and j.state != JobState.QUEUED)
        queued = sum(1 for j in jobs if j.state == JobState.QUEUED)
        return {"active": active, "queued": queued, "total": len(jobs)}

    def shutdown(self, timeout: float = 10.0) -> bool:
        """Stop accepting jobs, cancel everything, join workers. Returns True if all workers exited."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            try:
                if job.state in ACTIVE_JOB_STATES:
                    self.cancel(job.job_id)
            except InvalidJobState:
                pass
        for _ in self._workers:
            self._pending.put(None)
        deadline = time.monotonic() + timeout
        for worker in self._workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        clean = not any(w.is_alive() for w in self._workers)
        log.info("Job queue shut down", extra={"clean": clean})
        return clean

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _snapshot_unlocked(job: Job) -> JobStatus:
        return JobStatus(job_id=job.job_id, job_type=job.request.job_type, state=job.state,
                         progress=job.progress, message=job.message, label=job.request.label,
                         created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
                         cancel_requested=job.cancel_event.is_set(), result=job.result, error=job.error)

    def _job(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def _prune_history_locked(self) -> None:
        excess = len(self._jobs) - self._history_limit
        if excess <= 0:
            return
        for job_id in [jid for jid, j in self._jobs.items() if j.state in TERMINAL_JOB_STATES][:excess]:
            del self._jobs[job_id]

    def _worker_loop(self) -> None:
        while True:
            job = self._pending.get()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception:  # pragma: no cover - _run_job already guards
                log.exception("Unexpected worker error", extra={"job_id": job.job_id})

    def _run_job(self, job: Job) -> None:
        with job.lock:
            if job.state != JobState.QUEUED:  # cancelled while queued
                return
            job.state = JobState.RUNNING
            job.started_at = time.time()
            job.message = "Running"
        processor = self._tasks.get(job.request.job_type)

        def report(progress: float, message: str) -> None:
            with job.lock:
                job.progress = progress
                if message:
                    job.message = message

        def on_pause_state(paused: bool) -> None:
            with job.lock:
                if paused and job.state == JobState.RUNNING:
                    job.state, job.message = JobState.PAUSED, "Paused"
                elif not paused and job.state == JobState.PAUSED:
                    job.state, job.message = JobState.RUNNING, "Resumed"

        ctx = JobContext(job.job_id, job.params, report, job.cancel_event, job.resume_event, on_pause_state)
        try:
            result = processor.run(ctx)
            if job.cancel_event.is_set():
                raise JobCancelled()
        except JobCancelled:
            with job.lock:
                job.state, job.message, job.finished_at = JobState.CANCELLED, "Cancelled", time.time()
            log.info("Job cancelled", extra={"job_id": job.job_id})
        except Exception as exc:
            log.exception("Job failed", extra={"job_id": job.job_id})
            with job.lock:
                job.state = JobState.FAILED
                job.message = "Failed: %s" % exc
                job.error = ErrorInfo(ErrorCode.INTERNAL_ERROR.value, str(exc), {"exception": type(exc).__name__})
                job.finished_at = time.time()
        else:
            with job.lock:
                job.state, job.progress, job.message = JobState.COMPLETED, 1.0, "Completed"
                job.result = result if isinstance(result, dict) else {"value": result}
                job.finished_at = time.time()
            log.info("Job completed", extra={"job_id": job.job_id})
