"""ConnectionManager + JobManager against real engine processes (no Maya, no Qt)."""
import os
import signal
import subprocess
import sys
import time

import pytest

from helpers import ROOT, wait_until
from maya_plugin.communication.connection_manager import ConnectionManager, build_engine_env
from maya_plugin.communication.job_manager import JobManager
from maya_plugin.core.event_manager import EventManager, Events
from shared.constants.engine_constants import ConnectionState, JobState
from shared.paths import session_file

pytestmark = pytest.mark.process


@pytest.fixture
def services(plugin_config):
    events = EventManager()
    states, messages, updates = [], [], []
    events.subscribe(Events.CONNECTION_STATE_CHANGED, lambda s: states.append(s.state))
    events.subscribe(Events.ENGINE_MESSAGE, messages.append)
    events.subscribe(Events.JOB_UPDATED, updates.append)
    connection = ConnectionManager(plugin_config, events)
    jobs = JobManager(connection, plugin_config, events)
    yield connection, jobs, states, messages, updates
    jobs.shutdown()
    connection.shutdown(stop_owned_engine=True)


def test_start_connect_submit_monitor_cancel_stop(services):
    connection, jobs, states, messages, updates = services
    assert connection.state == ConnectionState.OFFLINE
    future = connection.start_engine()
    assert not future.done() or future.result() is True  # returned immediately (non-blocking)
    assert future.result(30) is True
    assert connection.state == ConnectionState.CONNECTED
    assert states[:2] == [ConnectionState.STARTING, ConnectionState.CONNECTED]
    assert connection.owns_engine and connection.status.health.engine_version == "0.1.0"
    pid = connection.status.engine_pid
    assert pid != os.getpid()  # independent process

    status = jobs.submit_test_job().result(10)
    assert status is not None and status.job_type == "simulated"
    assert wait_until(lambda: (jobs.get(status.job_id).progress or 0) > 0.02, timeout=10)
    jobs.cancel(status.job_id).result(5)
    assert wait_until(lambda: jobs.get(status.job_id).state == JobState.CANCELLED, timeout=5)
    seen_states = [u.state for u in updates if u.job_id == status.job_id]
    assert JobState.RUNNING in seen_states and seen_states[-1] == JobState.CANCELLED

    assert connection.health_check().result(5).active_jobs == 0
    assert connection.stop_engine().result(20) is True
    assert connection.state == ConnectionState.OFFLINE
    assert not connection.owns_engine and not session_file().exists()


def test_restart_gives_new_process(services):
    connection, _jobs, *_ = services
    assert connection.start_engine().result(30)
    first = connection.status.engine_pid
    assert connection.restart_engine().result(60)
    assert connection.state == ConnectionState.CONNECTED and connection.status.engine_pid != first


def test_engine_crash_is_detected(services):
    connection, jobs, states, messages, _ = services
    assert connection.start_engine().result(30)
    pid = connection.status.engine_pid
    os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    assert wait_until(lambda: connection.state == ConnectionState.OFFLINE, timeout=10)
    assert any("Lost connection" in m["message"] for m in messages)
    assert jobs.submit_test_job().result(5) is None  # fails gracefully, no exception


def test_connect_disconnect_external_engine(services, tmp_path):
    connection, jobs, *_ = services
    token = "e" * 48
    proc = subprocess.Popen([sys.executable, "-m", "ai_engine.main", "--port", "0", "--log-dir", str(tmp_path)],
                            cwd=str(ROOT), env=build_engine_env(os.environ, ROOT, token),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_until(lambda: session_file().exists(), timeout=20)
        assert connection.connect().result(10) is True
        assert not connection.owns_engine
        assert connection.disconnect().result(5) is True
        assert connection.state == ConnectionState.OFFLINE and proc.poll() is None  # engine keeps running
        assert connection.start_engine().result(10) is True     # reuses the running engine
        assert connection.status.engine_pid == proc.pid
        assert connection.stop_engine().result(20) is True      # explicit stop also stops external engine
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_startup_failure_reports_offline(services, plugin_config, tmp_path):
    connection, *_rest = services
    messages = _rest[2]
    broken = tmp_path / "engine_python.py"
    # Use a real interpreter but break the engine config so the process exits immediately.
    bad_config = tmp_path / "bad.json"
    bad_config.write_text('{"server": {"host": "0.0.0.0"}}')
    plugin_config.set("engine.config_file", str(bad_config), save=False)
    start = time.time()
    assert connection.start_engine().result(30) is False
    assert connection.state == ConnectionState.OFFLINE
    assert time.time() - start < 20
    assert any("exited during startup" in m["message"] for m in messages)
    assert not broken.exists()


def test_owned_engine_stopped_on_shutdown(plugin_config):
    events = EventManager()
    connection = ConnectionManager(plugin_config, events)
    assert connection.start_engine().result(30)
    pid = connection.status.engine_pid
    connection.shutdown()  # what Maya exit triggers
    from shared.process_utils import pid_exists
    assert wait_until(lambda: not pid_exists(pid), timeout=15)
