"""Client-side job tracking: submit, monitor (polling), pause/resume/cancel.

All network calls run on background threads. Status changes are published as
``Events.JOB_UPDATED`` (payload: :class:`JobStatus`); the UI bridge delivers
them to widgets on the main thread. Nothing here touches the Maya scene.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from maya_plugin.communication.connection_manager import ConnectionManager
from maya_plugin.communication.contracts import JobRequest, JobStatus, make_test_job_request
from maya_plugin.communication.engine_client import EngineClient, EngineConnectionError, EngineError
from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.logger import get_logger
from shared.constants.engine_constants import ConnectionState

log = get_logger("jobs")


class JobManager:
    def __init__(self, connection: ConnectionManager, config: ConfigManager, events: EventManager,
                 client_version: str = "") -> None:
        self._connection = connection
        self._config = config
        self._events = events
        self._client_version = client_version
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mait-jobs")
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobStatus] = {}
        self._tracked: Dict[str, JobStatus] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._monitor: Optional[threading.Thread] = None
        self._closed = False
        events.subscribe(Events.CONNECTION_STATE_CHANGED, self._on_connection_state)

    # ------------------------------------------------------------------ queries
    def jobs(self) -> List[JobStatus]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def get(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            return self._jobs.get(job_id)

    def active_job_ids(self) -> List[str]:
        with self._lock:
            return list(self._tracked)

    # ------------------------------------------------------------------ commands (non-blocking)
    def submit(self, request: JobRequest) -> Future:
        return self._run("submit", lambda client: self._submit_sync(client, request))

    def submit_test_job(self) -> Future:
        request = make_test_job_request(
            duration_seconds=float(self._config.get("jobs.test_job_duration_seconds", 20)),
            steps=int(self._config.get("jobs.test_job_steps", 100)), client_version=self._client_version)
        return self.submit(request)

    def cancel(self, job_id: str) -> Future:
        return self._run("cancel", lambda client: self._apply(client.cancel_job(job_id)), job_id)

    def pause(self, job_id: str) -> Future:
        return self._run("pause", lambda client: self._apply(client.pause_job(job_id)), job_id)

    def resume(self, job_id: str) -> Future:
        return self._run("resume", lambda client: self._apply(client.resume_job(job_id)), job_id)

    def refresh(self, job_id: str) -> Future:
        return self._run("refresh", lambda client: self._apply(client.get_job(job_id)), job_id)

    def cancel_all(self) -> List[Future]:
        return [self.cancel(job_id) for job_id in self.active_job_ids()]

    def shutdown(self) -> None:
        self._closed = True
        self._stop.set()
        self._wake.set()
        if self._monitor is not None:
            self._monitor.join(timeout=2.0)
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------ internals
    def _run(self, action: str, fn: Callable[[EngineClient], Optional[JobStatus]],
             job_id: str = "") -> Future:
        def task() -> Optional[JobStatus]:
            client = self._connection.client
            if client is None:
                self._error(job_id, "Cannot %s job: engine not connected" % action)
                return None
            try:
                return fn(client)
            except EngineError as exc:
                self._error(job_id, "Job %s failed: %s" % (action, exc))
                return None
            except Exception as exc:
                log.exception("Unexpected job manager error")
                self._error(job_id, "Job %s failed unexpectedly: %s" % (action, exc))
                return None

        if self._closed:
            future: Future = Future()
            future.set_result(None)
            return future
        return self._executor.submit(task)

    def _submit_sync(self, client: EngineClient, request: JobRequest) -> JobStatus:
        status = client.submit_job(request)
        log.info("Job submitted: %s (%s)" % (status.job_id[:8], status.job_type),
                 extra={"job_id": status.job_id})
        self._apply(status)
        self._ensure_monitor()
        return status

    def _apply(self, status: JobStatus) -> JobStatus:
        with self._lock:
            previous = self._jobs.get(status.job_id)
            self._jobs[status.job_id] = status
            if status.is_terminal:
                self._tracked.pop(status.job_id, None)
            else:
                self._tracked[status.job_id] = status
        if previous is None or previous.to_dict() != status.to_dict():
            self._events.publish(Events.JOB_UPDATED, status)
            if status.is_terminal and (previous is None or not previous.is_terminal):
                log.info("Job %s %s" % (status.job_id[:8], status.state.value), extra={"job_id": status.job_id})
        self._wake.set()
        return status

    def _ensure_monitor(self) -> None:
        with self._lock:
            if self._monitor is not None and self._monitor.is_alive():
                return
            self._stop.clear()
            self._monitor = threading.Thread(target=self._monitor_loop, name="mait-job-monitor", daemon=True)
            self._monitor.start()

    def _monitor_loop(self) -> None:
        interval = float(self._config.get("jobs.poll_interval_seconds", 0.5))
        while not self._stop.is_set():
            ids = self.active_job_ids()
            if not ids:
                self._wake.clear()
                self._wake.wait(5.0)
                if not self.active_job_ids():
                    continue
            client = self._connection.client
            if client is None:
                self._stop.wait(interval)
                continue
            for job_id in ids:
                if self._stop.is_set():
                    return
                try:
                    self._apply(client.get_job(job_id))
                except EngineConnectionError:
                    break  # connection manager will flip state; keep tracking for reconnect
                except EngineError as exc:
                    if exc.status == 404:  # engine restarted and forgot the job
                        with self._lock:
                            self._tracked.pop(job_id, None)
                    self._error(job_id, "Monitoring job failed: %s" % exc)
            self._stop.wait(interval)

    def _on_connection_state(self, status: object) -> None:
        state = getattr(status, "state", None)
        if state == ConnectionState.CONNECTED and self.active_job_ids():
            self._ensure_monitor()

    def _error(self, job_id: str, message: str) -> None:
        log.error(message, extra={"job_id": job_id})
        self._events.publish(Events.JOB_ERROR, {"job_id": job_id, "message": message})
