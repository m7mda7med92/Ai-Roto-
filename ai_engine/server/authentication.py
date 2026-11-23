"""Session-token authentication and Host-header validation."""
from __future__ import annotations

import hmac
import secrets
from typing import Optional

from shared.constants.engine_constants import ALLOWED_HOST_HEADERS, AUTH_SCHEME

MIN_TOKEN_LENGTH = 32


def generate_token() -> str:
    """Return a new cryptographically random session token."""
    return secrets.token_urlsafe(32)


def validate_token_strength(token: Optional[str]) -> str:
    if not token or len(token) < MIN_TOKEN_LENGTH:
        raise ValueError("session token must be at least %d characters" % MIN_TOKEN_LENGTH)
    return token


class Authenticator:
    """Validates ``Authorization: Bearer <token>`` with a constant-time compare."""

    def __init__(self, token: str) -> None:
        self._token = validate_token_strength(token).encode("utf-8")

    def is_authorized(self, header_value: Optional[str]) -> bool:
        if not header_value:
            return False
        scheme, _, provided = header_value.strip().partition(" ")
        if scheme.lower() != AUTH_SCHEME.lower() or not provided:
            return False
        return hmac.compare_digest(provided.strip().encode("utf-8"), self._token)


def is_allowed_host_header(host_header: Optional[str]) -> bool:
    """Accept only loopback Host headers (DNS-rebinding protection)."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal - not served
        return False
    host = host.rsplit(":", 1)[0] if ":" in host else host
    return host in ALLOWED_HOST_HEADERS
