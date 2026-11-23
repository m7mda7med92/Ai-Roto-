"""Strict JSON helpers (no NaN/Infinity, UTF-8, enums -> values)."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    raise TypeError("Object of type %s is not JSON serializable" % type(obj).__name__)


def dumps(obj: Any) -> str:
    """Serialize ``obj`` to compact, strict JSON."""
    return json.dumps(obj, default=_default, allow_nan=False, separators=(",", ":"), ensure_ascii=False)


def _reject_constant(name: str) -> Any:
    raise ValueError("Invalid JSON constant: %s" % name)


def loads(text: Any) -> Any:
    """Parse strict JSON (``bytes`` are decoded as UTF-8). Raises ``ValueError``."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    return json.loads(text, parse_constant=_reject_constant)
