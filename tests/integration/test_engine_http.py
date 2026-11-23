"""HTTP API against a real (in-process) engine server bound to 127.0.0.1."""
import http.client
import json

import pytest

from helpers import TEST_TOKEN, wait_until
from maya_plugin.communication.contracts import make_test_job_request
from maya_plugin.communication.engine_client import EngineApiError, EngineAuthError
from shared.constants.engine_constants import JobState
from shared.contracts import JobRequest
from shared.version import TOOLKIT_VERSION


def raw(engine, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", engine.port, timeout=5)
    base = {"Authorization": "Bearer " + TEST_TOKEN}
    base.update(headers or {})
    conn.request(method, path, body=body, headers=base)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_server_bound_to_loopback_only(engine):
    assert engine.manager.host == "127.0.0.1"


def test_health_fields(engine):
    health = engine.client().health()
    assert health.status == "ok" and health.api_version == "v1" and health.engine_version == TOOLKIT_VERSION
    assert health.python_version and isinstance(health.gpu_available, bool)
    assert isinstance(health.cuda_available, bool) and health.active_jobs == 0
    assert "simulated" in health.job_types


@pytest.mark.parametrize("header", [None, "Bearer wrong-token-wrong-token-wrong-token", "Basic abc"])
def test_authentication_required(engine, header):
    headers = {"Authorization": header} if header else {"Authorization": ""}
    status, data = raw(engine, "GET", "/api/v1/health", headers=headers)
    assert status == 401 and data == {"ok": False, "error": {"code": "unauthorized",
                                                             "message": "missing or invalid session token",
                                                             "details": {}}}
    with pytest.raises(EngineAuthError):
        engine.client(token="z" * 43).health()


def test_foreign_host_header_rejected(engine):
    status, data = raw(engine, "GET", "/api/v1/health", headers={"Host": "evil.example.com"})
    assert status == 403 and data["error"]["code"] == "forbidden_host"


def test_structured_errors(engine):
    assert raw(engine, "GET", "/api/v1/nope")[0] == 404
    status, data = raw(engine, "DELETE", "/api/v1/health")
    assert status == 405 and data["error"]["code"] == "method_not_allowed"
    status, data = raw(engine, "POST", "/api/v1/jobs", body=b"{oops", headers={"Content-Type": "application/json"})
    assert status == 400 and data["error"]["code"] == "invalid_json"
    status, data = raw(engine, "POST", "/api/v1/jobs", body=json.dumps({"job_type": "simulated",
                                                                        "params": {"steps": "many"}}))
    assert status == 422 and data["error"]["code"] == "validation_error" and data["error"]["details"]["field"] == "steps"
    status, data = raw(engine, "POST", "/api/v1/jobs", body=json.dumps({"job_type": "teleport"}))
    assert status == 422 and data["error"]["code"] == "unknown_job_type"
    assert "simulated" in data["error"]["details"]["available"]
    assert {"model_install", "media_proxies", "segment_frame", "roto_track"} <= set(data["error"]["details"]["available"])
    status, data = raw(engine, "GET", "/api/v1/jobs/doesnotexist")
    assert status == 404 and data["error"]["code"] == "not_found"
    # Declared size over the limit is rejected before the body is read.
    status, data = raw(engine, "POST", "/api/v1/jobs", body=b"{}", headers={"Content-Length": str(2 * 1024 * 1024)})
    assert status == 413 and data["error"]["code"] == "payload_too_large"


def test_submit_progress_complete(engine):
    client = engine.client()
    job = client.submit_job(make_test_job_request(duration_seconds=0.5, steps=10))
    assert job.state == JobState.QUEUED
    progresses = []
    assert wait_until(lambda: progresses.append(client.get_job(job.job_id).progress) or
                      client.get_job(job.job_id).is_terminal)
    final = client.get_job(job.job_id)
    assert final.state == JobState.COMPLETED and final.result["simulated"] is True
    assert any(0 < p < 1 for p in progresses)
    assert job.job_id in [j.job_id for j in client.list_jobs()]


def test_cancel_pause_resume_over_http(engine):
    client = engine.client()
    job = client.submit_job(JobRequest("simulated", {"duration_seconds": 10, "steps": 100}))
    assert wait_until(lambda: client.get_job(job.job_id).state == JobState.RUNNING)
    client.pause_job(job.job_id)
    assert wait_until(lambda: client.get_job(job.job_id).state == JobState.PAUSED)
    client.resume_job(job.job_id)
    assert wait_until(lambda: client.get_job(job.job_id).state == JobState.RUNNING)
    assert client.cancel_job(job.job_id).cancel_requested
    assert wait_until(lambda: client.get_job(job.job_id).state == JobState.CANCELLED, timeout=2)
    with pytest.raises(EngineApiError) as info:
        client.pause_job(job.job_id)
    assert info.value.status == 409 and info.value.code == "invalid_state"


def test_failed_job_returns_structured_error(engine):
    client = engine.client()
    job = client.submit_job(JobRequest("simulated", {"duration_seconds": 0.2, "steps": 4, "fail_at_progress": 0.5}))
    assert wait_until(lambda: client.get_job(job.job_id).is_terminal)
    final = client.get_job(job.job_id)
    assert final.state == JobState.FAILED and final.error.message.startswith("Simulated failure")


def test_shutdown_endpoint_stops_server_and_removes_session(tmp_path):
    from helpers import InProcessEngine
    running = InProcessEngine(tmp_path).__enter__()
    client = running.client()
    job = client.submit_job(JobRequest("simulated", {"duration_seconds": 30, "steps": 300}))
    client.shutdown()
    running.thread.join(timeout=15)
    assert not running.thread.is_alive()
    assert not running.session_file.exists()
    assert running.manager.jobs.get(job.job_id).state == JobState.CANCELLED
