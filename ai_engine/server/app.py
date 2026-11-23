"""HTTP server bound exclusively to 127.0.0.1.

Request pipeline: Host check -> token auth -> route -> JSON body -> handler -> JSON envelope.
Internal errors are logged with a traceback but never leak details to the client.
"""
from __future__ import annotations

import logging
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Tuple
from urllib.parse import urlsplit

from ai_engine.server.authentication import Authenticator, is_allowed_host_header
from ai_engine.server.routes import ApiError, Request, Router
from shared.constants.engine_constants import ALLOWED_BIND_HOSTS, AUTH_HEADER, ErrorCode
from shared.contracts.envelopes import error_envelope, success_envelope
from shared.contracts.serialization import dumps, loads
from shared.version import TOOLKIT_ID, TOOLKIT_VERSION

log = logging.getLogger("ai_engine.http")


class EngineHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # never steal a port another process is using

    def __init__(self, host: str, port: int, router: Router, authenticator: Authenticator,
                 max_request_bytes: int = 1048576, request_timeout: float = 30.0) -> None:
        if host not in ALLOWED_BIND_HOSTS:
            raise ValueError("refusing to bind to %r: only 127.0.0.1 is allowed" % host)
        self.router = router
        self.authenticator = authenticator
        self.max_request_bytes = max_request_bytes
        self.request_timeout = request_timeout
        super().__init__((host, port), EngineRequestHandler)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: prevent port hijacking
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
        super().server_bind()

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class EngineRequestHandler(BaseHTTPRequestHandler):
    server: EngineHTTPServer  # type: ignore[assignment]
    server_version = "%s/%s" % (TOOLKIT_ID, TOOLKIT_VERSION)
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        self.timeout = self.server.request_timeout
        super().setup()

    # --------------------------------------------------------------- verbs
    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    # --------------------------------------------------------------- pipeline
    def _handle(self, method: str) -> None:
        started = time.monotonic()
        status = 500
        try:
            status, payload = self._dispatch(method)
        except ApiError as exc:
            status, payload = exc.status, error_envelope(exc.code, exc.message, exc.details)
        except Exception:
            log.exception("Unhandled error", extra={"method": method, "path": self.path})
            status, payload = 500, error_envelope(ErrorCode.INTERNAL_ERROR, "internal engine error")
        self._send_json(status, payload)
        log.debug("request", extra={"method": method, "path": urlsplit(self.path).path, "status": status,
                                    "ms": round((time.monotonic() - started) * 1000, 2)})

    def _dispatch(self, method: str) -> Tuple[int, Any]:
        if not is_allowed_host_header(self.headers.get("Host")):
            raise ApiError(403, ErrorCode.FORBIDDEN_HOST, "Host header not allowed")
        if not self.server.authenticator.is_authorized(self.headers.get(AUTH_HEADER)):
            raise ApiError(401, ErrorCode.UNAUTHORIZED, "missing or invalid session token")
        path = urlsplit(self.path).path.rstrip("/") or "/"
        route, params = self.server.router.resolve(method, path)
        body = self._read_body() if method in ("POST", "PUT", "PATCH") else None
        status, data = route.handler(Request(method=method, path=path, params=params, body=body))
        return status, success_envelope(data)

    def _read_body(self) -> Any:
        length_header = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "chunked bodies are not supported")
        try:
            length = int(length_header or 0)
        except ValueError:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "invalid Content-Length")
        if length < 0:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "invalid Content-Length")
        if length > self.server.max_request_bytes:
            self.close_connection = True
            raise ApiError(413, ErrorCode.PAYLOAD_TOO_LARGE, "request body too large")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(400, ErrorCode.INVALID_JSON, "request body is not valid JSON: %s" % exc)

    def _send_json(self, status: int, payload: Any) -> None:
        body = dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("client disconnected before response was sent")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - silence default stderr logging
        return


def create_server(host: str, port: int, router: Router, token: str, max_request_bytes: int = 1048576,
                  request_timeout: float = 30.0, authenticator: Optional[Authenticator] = None) -> EngineHTTPServer:
    return EngineHTTPServer(host, port, router, authenticator or Authenticator(token),
                            max_request_bytes=max_request_bytes, request_timeout=request_timeout)
