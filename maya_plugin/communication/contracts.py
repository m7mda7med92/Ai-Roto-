"""Client-side view of the wire contracts (re-exported from :mod:`shared.contracts`).

UI and module code import contracts from here so the plugin has a single
place to adapt if the shared contract package ever moves or is versioned.
"""
from shared.constants.engine_constants import ConnectionState, ErrorCode, JobState  # noqa: F401
from shared.contracts import (  # noqa: F401
    ContractError,
    ErrorInfo,
    GpuDevice,
    HealthInfo,
    JobRequest,
    JobStatus,
    dumps,
    loads,
)

SIMULATED_JOB_TYPE = "simulated"


def make_test_job_request(duration_seconds: float = 20.0, steps: int = 100, label: str = "Test Processing",
                          client_version: str = "") -> JobRequest:
    """Build the request used by the UI's *Test Processing* button."""
    return JobRequest(job_type=SIMULATED_JOB_TYPE,
                      params={"duration_seconds": float(duration_seconds), "steps": int(steps)},
                      label=label, client={"name": "maya", "version": client_version}).validate()
