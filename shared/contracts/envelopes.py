"""Response envelopes.

Every engine response body is one of::

    {"ok": true,  "data": {...}}
    {"ok": false, "error": {"code": str, "message": str, "details": {...}}}
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from shared.contracts.errors import ContractError, ErrorInfo


def success_envelope(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def error_envelope(code: Any, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    code_value = getattr(code, "value", code)
    return {"ok": False, "error": ErrorInfo(str(code_value), message, details or {}).to_dict()}


def parse_envelope(payload: Any) -> Tuple[bool, Any]:
    """Validate an envelope. Returns ``(True, data)`` or ``(False, ErrorInfo)``."""
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise ContractError("response is not a valid envelope")
    if payload["ok"]:
        if "data" not in payload:
            raise ContractError("success envelope missing 'data'")
        return True, payload["data"]
    return False, ErrorInfo.from_dict(payload.get("error"))
