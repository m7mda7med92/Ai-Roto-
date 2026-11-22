"""Structured error objects used on the wire and inside both processes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ContractError(ValueError):
    """Raised when a payload does not satisfy a contract."""

    def __init__(self, message: str, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


@dataclass
class ErrorInfo:
    """A structured error: ``{"code", "message", "details"}``."""

    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": dict(self.details)}

    @classmethod
    def from_dict(cls, data: Any) -> "ErrorInfo":
        if not isinstance(data, dict):
            raise ContractError("error must be an object", "error")
        code = data.get("code")
        message = data.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise ContractError("error.code and error.message must be strings", "error")
        details = data.get("details") or {}
        if not isinstance(details, dict):
            raise ContractError("error.details must be an object", "error.details")
        return cls(code=code, message=message, details=details)
