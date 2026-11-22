"""Protocol-level constants: auth, hosts, job states, error codes."""
from __future__ import annotations

from enum import Enum

# --------------------------------------------------------------------------- network
#: The engine may only ever bind to the IPv4 loopback address.
DEFAULT_ENGINE_HOST = "127.0.0.1"
ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1"})
#: Accepted ``Host`` header names (port stripped). Guards against DNS-rebinding.
ALLOWED_HOST_HEADERS = frozenset({"127.0.0.1", "localhost"})

# --------------------------------------------------------------------------- auth
AUTH_HEADER = "Authorization"
AUTH_SCHEME = "Bearer"

# --------------------------------------------------------------------------- environment variables
#: Session token handed from the launcher to the engine (never on the command line).
ENV_ENGINE_TOKEN = "MAIT_ENGINE_TOKEN"
#: Overrides the per-user data directory (settings, logs, runtime files).
ENV_USER_DIR = "MAIT_USER_DIR"
#: Overrides the location of the engine session file.
ENV_SESSION_FILE = "MAIT_SESSION_FILE"

SESSION_FILE_NAME = "engine_session.json"


class JobState(str, Enum):
    """Lifecycle states of an engine job."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_JOB_STATES = frozenset({JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED})
ACTIVE_JOB_STATES = frozenset({JobState.QUEUED, JobState.RUNNING, JobState.PAUSED, JobState.CANCELLING})


class ErrorCode(str, Enum):
    """Machine-readable error codes used in structured error responses."""

    BAD_REQUEST = "bad_request"
    INVALID_JSON = "invalid_json"
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN_HOST = "forbidden_host"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    UNKNOWN_JOB_TYPE = "unknown_job_type"
    INVALID_STATE = "invalid_state"
    QUEUE_FULL = "queue_full"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    SHUTTING_DOWN = "shutting_down"
    INTERNAL_ERROR = "internal_error"
    # client-side only codes
    CONNECTION_FAILED = "connection_failed"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"


class ConnectionState(str, Enum):
    """Engine connection state as seen from Maya. Maps 1:1 to the UI status colours."""

    NOT_CONFIGURED = "not_configured"  # GRAY   - no engine Python configured/found
    OFFLINE = "offline"                # RED    - configured, but not reachable
    STARTING = "starting"              # YELLOW - launching / connecting
    CONNECTED = "connected"            # GREEN  - healthy and authenticated
    STOPPING = "stopping"              # YELLOW - shutting down
