"""Typed contracts for jobs and health information.

Each contract is a dataclass with ``to_dict()`` / ``from_dict()`` that performs
validation, so both processes reject malformed payloads the same way. The
matching language-neutral JSON Schemas live in ``shared/schemas`` for future
non-Python clients (Nuke, 3DEqualizer).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shared.constants.engine_constants import JobState, TERMINAL_JOB_STATES
from shared.contracts.errors import ContractError, ErrorInfo

MAX_JOB_TYPE_LENGTH = 64
MAX_LABEL_LENGTH = 200
_JOB_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.\-]*$")


def _require(data: Dict[str, Any], key: str, kind: Any, optional: bool = False) -> Any:
    if key not in data or data[key] is None:
        if optional:
            return None
        raise ContractError("missing required field '%s'" % key, key)
    value = data[key]
    # bool is a subclass of int; never accept it where a number is expected.
    if kind in (int, float, (int, float)) and isinstance(value, bool):
        raise ContractError("field '%s' must be a number" % key, key)
    if not isinstance(value, kind):
        raise ContractError("field '%s' has invalid type" % key, key)
    return value


def _as_object(data: Any, name: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ContractError("%s must be a JSON object" % name)
    return data


@dataclass
class JobRequest:
    """``POST /api/v1/jobs`` request body."""

    job_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    label: str = ""
    client: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "JobRequest":
        if not isinstance(self.job_type, str) or not self.job_type:
            raise ContractError("job_type must be a non-empty string", "job_type")
        if len(self.job_type) > MAX_JOB_TYPE_LENGTH or not _JOB_TYPE_RE.match(self.job_type):
            raise ContractError("job_type has invalid format", "job_type")
        if not isinstance(self.params, dict):
            raise ContractError("params must be an object", "params")
        if not isinstance(self.label, str) or len(self.label) > MAX_LABEL_LENGTH:
            raise ContractError("label must be a string of at most %d chars" % MAX_LABEL_LENGTH, "label")
        if not isinstance(self.client, dict):
            raise ContractError("client must be an object", "client")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {"job_type": self.job_type, "params": dict(self.params), "label": self.label, "client": dict(self.client)}

    @classmethod
    def from_dict(cls, data: Any) -> "JobRequest":
        data = _as_object(data, "job request")
        return cls(
            job_type=_require(data, "job_type", str),
            params=_require(data, "params", dict, optional=True) or {},
            label=_require(data, "label", str, optional=True) or "",
            client=_require(data, "client", dict, optional=True) or {},
        ).validate()


@dataclass
class JobStatus:
    """Job status as returned by ``GET /api/v1/jobs/{job_id}``."""

    job_id: str
    job_type: str
    state: JobState
    progress: float = 0.0
    message: str = ""
    label: str = ""
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_requested: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorInfo] = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "label": self.label,
            "state": self.state.value,
            "progress": round(float(self.progress), 4),
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
            "result": self.result,
            "error": self.error.to_dict() if self.error else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "JobStatus":
        data = _as_object(data, "job status")
        state_raw = _require(data, "state", str)
        try:
            state = JobState(state_raw)
        except ValueError:
            raise ContractError("unknown job state '%s'" % state_raw, "state")
        progress = float(_require(data, "progress", (int, float)))
        if not 0.0 <= progress <= 1.0:
            raise ContractError("progress must be within [0, 1]", "progress")
        error = data.get("error")
        return cls(
            job_id=_require(data, "job_id", str),
            job_type=_require(data, "job_type", str),
            state=state,
            progress=progress,
            message=_require(data, "message", str, optional=True) or "",
            label=_require(data, "label", str, optional=True) or "",
            created_at=float(_require(data, "created_at", (int, float), optional=True) or 0.0),
            started_at=_require(data, "started_at", (int, float), optional=True),
            finished_at=_require(data, "finished_at", (int, float), optional=True),
            cancel_requested=bool(data.get("cancel_requested", False)),
            result=_require(data, "result", dict, optional=True),
            error=ErrorInfo.from_dict(error) if error else None,
        )


@dataclass
class GpuDevice:
    index: int
    name: str
    memory_total_mb: Optional[int] = None
    driver_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "name": self.name, "memory_total_mb": self.memory_total_mb,
                "driver_version": self.driver_version}

    @classmethod
    def from_dict(cls, data: Any) -> "GpuDevice":
        data = _as_object(data, "gpu device")
        return cls(index=int(_require(data, "index", int)), name=_require(data, "name", str),
                   memory_total_mb=_require(data, "memory_total_mb", int, optional=True),
                   driver_version=_require(data, "driver_version", str, optional=True))


@dataclass
class HealthInfo:
    """``GET /api/v1/health`` payload."""

    status: str
    engine_version: str
    api_version: str
    python_version: str
    platform: str
    pid: int
    uptime_seconds: float
    gpu_available: bool
    cuda_available: bool
    gpu_devices: List[GpuDevice] = field(default_factory=list)
    gpu_detection_method: str = "none"
    active_jobs: int = 0
    queued_jobs: int = 0
    job_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "engine_version": self.engine_version,
            "api_version": self.api_version,
            "python_version": self.python_version,
            "platform": self.platform,
            "pid": self.pid,
            "uptime_seconds": round(self.uptime_seconds, 3),
            "gpu_available": self.gpu_available,
            "cuda_available": self.cuda_available,
            "gpu_devices": [d.to_dict() for d in self.gpu_devices],
            "gpu_detection_method": self.gpu_detection_method,
            "active_jobs": self.active_jobs,
            "queued_jobs": self.queued_jobs,
            "job_types": list(self.job_types),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "HealthInfo":
        data = _as_object(data, "health")
        return cls(
            status=_require(data, "status", str),
            engine_version=_require(data, "engine_version", str),
            api_version=_require(data, "api_version", str),
            python_version=_require(data, "python_version", str),
            platform=_require(data, "platform", str, optional=True) or "",
            pid=int(_require(data, "pid", int)),
            uptime_seconds=float(_require(data, "uptime_seconds", (int, float))),
            gpu_available=bool(_require(data, "gpu_available", bool)),
            cuda_available=bool(_require(data, "cuda_available", bool)),
            gpu_devices=[GpuDevice.from_dict(d) for d in (data.get("gpu_devices") or [])],
            gpu_detection_method=data.get("gpu_detection_method") or "none",
            active_jobs=int(_require(data, "active_jobs", int)),
            queued_jobs=int(data.get("queued_jobs") or 0),
            job_types=list(data.get("job_types") or []),
        )
