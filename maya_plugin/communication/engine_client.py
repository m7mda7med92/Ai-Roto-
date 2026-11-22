"""Synchronous HTTP client for the engine API (stdlib ``urllib`` only).

Every call blocks, so it must run on a background worker - never on Maya's
main thread. All failures are raised as :class:`EngineError` subclasses with a
machine-readable ``code``; nothing else escapes.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from shared.constants.engine_constants import ALLOWED_BIND_HOSTS, AUTH_HEADER, AUTH_SCHEME, ErrorCode
from shared.contracts.envelopes import parse_envelope
from shared.contracts.errors import ContractError
from shared.contracts.job_contracts import HealthInfo, JobRequest, JobStatus
from shared.contracts.serialization import dumps, loads
from shared.version import API_PREFIX


class EngineError(Exception):
    def __init__(self, code: str, message: str, status: Optional[int] = None,
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def __str__(self) -> str:
        return "%s (%s)" % (self.message, self.code)


class EngineConnectionError(EngineError):
    """Engine unreachable (not running, port closed, connection reset)."""


class EngineTimeoutError(EngineError):
    """Engine did not answer within the timeout."""


class EngineAuthError(EngineError):
    """Token rejected (401) or Host rejected (403)."""


class EngineResponseError(EngineError):
    """Engine answered with something that is not a valid envelope."""


class EngineApiError(EngineError):
    """Engine returned a structured error (4xx/5xx with an error envelope)."""


class EngineClient:
    def __init__(self, host: str, port: int, token: str, timeout: float = 5.0) -> None:
        if host not in ALLOWED_BIND_HOSTS:
            raise ValueError("engine host must be a loopback address, got %r" % host)
        self.base_url = "http://%s:%d%s" % (host, int(port), API_PREFIX)
        self._token = token
        self.timeout = timeout
        # Explicitly bypass any system/corporate HTTP proxy: traffic must stay on loopback.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ------------------------------------------------------------------ endpoints
    def health(self, timeout: Optional[float] = None) -> HealthInfo:
        return self._parse(HealthInfo, self._request("GET", "/health", timeout=timeout))

    def submit_job(self, request: JobRequest) -> JobStatus:
        return self._parse(JobStatus, self._request("POST", "/jobs", request.validate().to_dict()))

    def get_job(self, job_id: str) -> JobStatus:
        return self._parse(JobStatus, self._request("GET", "/jobs/%s" % job_id))

    def list_jobs(self) -> List[JobStatus]:
        data = self._request("GET", "/jobs")
        try:
            return [JobStatus.from_dict(item) for item in data["jobs"]]
        except (KeyError, TypeError, ContractError) as exc:
            raise EngineResponseError(ErrorCode.INVALID_RESPONSE.value, "invalid job list: %s" % exc)

    def cancel_job(self, job_id: str) -> JobStatus:
        return self._parse(JobStatus, self._request("POST", "/jobs/%s/cancel" % job_id, {}))

    def pause_job(self, job_id: str) -> JobStatus:
        return self._parse(JobStatus, self._request("POST", "/jobs/%s/pause" % job_id, {}))

    def resume_job(self, job_id: str) -> JobStatus:
        return self._parse(JobStatus, self._request("POST", "/jobs/%s/resume" % job_id, {}))

    def shutdown(self) -> None:
        self._request("POST", "/shutdown", {})

    # ------------------------------------------------------------------ transport
    @staticmethod
    def _parse(contract: Any, data: Any) -> Any:
        try:
            return contract.from_dict(data)
        except ContractError as exc:
            raise EngineResponseError(ErrorCode.INVALID_RESPONSE.value, "invalid %s: %s" % (contract.__name__, exc))

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 timeout: Optional[float] = None) -> Any:
        data = dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header(AUTH_HEADER, "%s %s" % (AUTH_SCHEME, self._token))
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        status: Optional[int] = None
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            finally:
                exc.close()
        except (socket.timeout, TimeoutError) as exc:
            raise EngineTimeoutError(ErrorCode.TIMEOUT.value, "engine did not respond in time: %s" % exc)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise EngineTimeoutError(ErrorCode.TIMEOUT.value, "engine did not respond in time")
            raise EngineConnectionError(ErrorCode.CONNECTION_FAILED.value, "cannot reach engine: %s" % reason)
        except (ConnectionError, OSError) as exc:
            raise EngineConnectionError(ErrorCode.CONNECTION_FAILED.value, "cannot reach engine: %s" % exc)
        return self._decode(status, raw)

    @staticmethod
    def _decode(status: Optional[int], raw: bytes) -> Any:
        try:
            ok, value = parse_envelope(loads(raw))
        except (ValueError, ContractError, UnicodeDecodeError):
            raise EngineResponseError(ErrorCode.INVALID_RESPONSE.value,
                                      "engine returned an invalid response (HTTP %s)" % status, status)
        if ok:
            return value
        error_cls = EngineAuthError if status in (401, 403) else EngineApiError
        raise error_cls(value.code, value.message, status, value.details)
