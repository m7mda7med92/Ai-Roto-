"""Job queue: submission, progress, cancellation, pause/resume, failures, shutdown."""
import pytest

from ai_engine.core.job_queue import InvalidJobState, JobNotFound, JobQueue, QueueClosed, QueueFull
from ai_engine.core.task_manager import UnknownJobType, build_default_task_manager
from helpers import wait_until
from shared.constants.engine_constants import JobState
from shared.contracts import ContractError, JobRequest


@pytest.fixture
def queue():
    q = JobQueue(build_default_task_manager(), max_concurrent_jobs=1, max_queued_jobs=3, history_limit=50)
    yield q
    q.shutdown(timeout=5)


def sim(duration=0.5, steps=10, **extra):
    return JobRequest("simulated", dict(duration_seconds=duration, steps=steps, **extra))


def test_job_completes_with_increasing_progress(queue):
    status = queue.submit(sim(0.4, 8))
    assert status.state == JobState.QUEUED and len(status.job_id) == 32
    seen = []
    assert wait_until(lambda: seen.append(queue.get(status.job_id).progress) or queue.get(status.job_id).is_terminal)
    final = queue.get(status.job_id)
    assert final.state == JobState.COMPLETED and final.progress == 1.0
    assert final.result["simulated"] is True and final.result["steps_completed"] == 8
    assert seen == sorted(seen) and any(0 < p < 1 for p in seen)


def test_job_ids_are_unique(queue):
    ids = {queue.submit(sim(0.0, 1)).job_id for _ in range(3)}
    assert len(ids) == 3


def test_cancel_running_job(queue):
    job = queue.submit(sim(10, 100))
    assert wait_until(lambda: queue.get(job.job_id).progress > 0.02)
    assert queue.cancel(job.job_id).cancel_requested
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.CANCELLED, timeout=2)
    assert queue.get(job.job_id).progress < 1.0


def test_cancel_queued_job_never_runs(queue):
    first = queue.submit(sim(10, 100))
    second = queue.submit(sim(1, 2))
    assert queue.cancel(second.job_id).state == JobState.CANCELLED
    queue.cancel(first.job_id)
    assert wait_until(lambda: queue.get(first.job_id).is_terminal)
    assert queue.get(second.job_id).started_at is None


def test_cancel_is_idempotent_but_not_after_completion(queue):
    job = queue.submit(sim(0.0, 1))
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.COMPLETED)
    with pytest.raises(InvalidJobState):
        queue.cancel(job.job_id)
    other = queue.submit(sim(10, 100))
    queue.cancel(other.job_id)
    queue.cancel(other.job_id)  # no error


def test_pause_and_resume(queue):
    job = queue.submit(sim(5, 100))
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.RUNNING)
    queue.pause(job.job_id)
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.PAUSED)
    frozen = queue.get(job.job_id).progress
    assert not wait_until(lambda: queue.get(job.job_id).progress > frozen, timeout=0.4)
    queue.resume(job.job_id)
    assert wait_until(lambda: queue.get(job.job_id).progress > frozen)
    queue.cancel(job.job_id)
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.CANCELLED)


def test_cancel_while_paused(queue):
    job = queue.submit(sim(5, 100))
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.RUNNING)
    queue.pause(job.job_id)
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.PAUSED)
    queue.cancel(job.job_id)
    assert wait_until(lambda: queue.get(job.job_id).state == JobState.CANCELLED, timeout=2)


def test_pause_requires_running(queue):
    job = queue.submit(sim(0.0, 1))
    assert wait_until(lambda: queue.get(job.job_id).is_terminal)
    with pytest.raises(InvalidJobState):
        queue.pause(job.job_id)


def test_failure_is_reported_structurally(queue):
    job = queue.submit(sim(0.2, 10, fail_at_progress=0.5))
    assert wait_until(lambda: queue.get(job.job_id).is_terminal)
    final = queue.get(job.job_id)
    assert final.state == JobState.FAILED
    assert final.error.code == "internal_error" and "Simulated failure" in final.error.message


def test_validation_and_unknown_type(queue):
    with pytest.raises(UnknownJobType):
        queue.submit(JobRequest("segment_everything"))
    with pytest.raises(ContractError):
        queue.submit(JobRequest("simulated", {"steps": 0}))
    with pytest.raises(ContractError):
        queue.submit(JobRequest("simulated", {"duration_seconds": -1}))
    with pytest.raises(JobNotFound):
        queue.get("nope")


def test_queue_full(queue):
    queue.submit(sim(10, 100))
    assert wait_until(lambda: queue.counts()["active"] == 1)
    for _ in range(3):
        queue.submit(sim(10, 100))
    with pytest.raises(QueueFull):
        queue.submit(sim(10, 100))


def test_shutdown_cancels_everything_and_closes(queue):
    running = queue.submit(sim(30, 300))
    queued = queue.submit(sim(30, 300))
    assert wait_until(lambda: queue.get(running.job_id).state == JobState.RUNNING)
    assert queue.shutdown(timeout=5) is True
    assert queue.get(running.job_id).state == JobState.CANCELLED
    assert queue.get(queued.job_id).state == JobState.CANCELLED
    with pytest.raises(QueueClosed):
        queue.submit(sim())


def test_history_is_pruned():
    q = JobQueue(build_default_task_manager(), max_concurrent_jobs=2, max_queued_jobs=50, history_limit=5)
    try:
        for _ in range(12):
            job = q.submit(sim(0.0, 1))
            assert wait_until(lambda: q.get(job.job_id).is_terminal)
        assert len(q.list()) <= 6
    finally:
        q.shutdown()
