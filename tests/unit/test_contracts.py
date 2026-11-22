"""JSON serialization and contract validation (shared by plugin and engine)."""
import math

import pytest

from shared.constants.engine_constants import JobState
from shared.contracts import (ContractError, ErrorInfo, GpuDevice, HealthInfo, JobRequest, JobStatus, dumps,
                              error_envelope, loads, parse_envelope, success_envelope)


def test_job_request_roundtrip():
    req = JobRequest("simulated", {"duration_seconds": 2.5, "steps": 5}, "label", {"name": "maya"})
    again = JobRequest.from_dict(loads(dumps(req.to_dict())))
    assert again == req


@pytest.mark.parametrize("payload, field", [
    ({}, "job_type"),
    ({"job_type": ""}, "job_type"),
    ({"job_type": "Bad Type"}, "job_type"),
    ({"job_type": "x" * 65}, "job_type"),
    ({"job_type": "simulated", "params": []}, "params"),
    ({"job_type": "simulated", "label": 5}, "label"),
    ({"job_type": "simulated", "client": "maya"}, "client"),
])
def test_job_request_rejects_invalid(payload, field):
    with pytest.raises(ContractError) as info:
        JobRequest.from_dict(payload)
    assert info.value.field_name == field


def test_job_request_rejects_non_object():
    with pytest.raises(ContractError):
        JobRequest.from_dict(["simulated"])


def test_job_status_roundtrip_with_error():
    status = JobStatus("abc", "simulated", JobState.FAILED, 0.5, "boom", "lbl", 1.0, 2.0, 3.0, True, None,
                       ErrorInfo("internal_error", "boom", {"exception": "RuntimeError"}))
    again = JobStatus.from_dict(loads(dumps(status)))
    assert again.to_dict() == status.to_dict()
    assert again.is_terminal


@pytest.mark.parametrize("change", [{"state": "exploded"}, {"progress": 1.5}, {"progress": True}, {"job_id": 7}])
def test_job_status_validation(change):
    data = JobStatus("abc", "simulated", JobState.RUNNING, 0.1).to_dict()
    data.update(change)
    with pytest.raises(ContractError):
        JobStatus.from_dict(data)


def test_health_roundtrip():
    health = HealthInfo("ok", "0.1.0", "v1", "3.11.4", "Windows 10", 42, 1.5, True, True,
                        [GpuDevice(0, "RTX 4090", 24564, "551.23")], "nvidia-smi", 1, 2, ["simulated"])
    again = HealthInfo.from_dict(loads(dumps(health)))
    assert again.to_dict() == health.to_dict()


def test_envelopes():
    assert parse_envelope(success_envelope({"a": 1})) == (True, {"a": 1})
    ok, err = parse_envelope(error_envelope("not_found", "missing", {"id": "x"}))
    assert not ok and err.code == "not_found" and err.details == {"id": "x"}
    with pytest.raises(ContractError):
        parse_envelope({"data": 1})
    with pytest.raises(ContractError):
        parse_envelope({"ok": False, "error": "oops"})


def test_strict_json_rejects_nan_both_ways():
    with pytest.raises(ValueError):
        dumps({"x": math.nan})
    with pytest.raises(ValueError):
        loads('{"x": NaN}')


def test_dumps_handles_enums_and_bytes_loads():
    assert loads(dumps({"s": JobState.RUNNING}).encode("utf-8")) == {"s": "running"}
