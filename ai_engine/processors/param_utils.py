"""Small validators shared by the Phase 02 processors."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from shared.contracts.errors import ContractError

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def req_str(params: Dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError("%s must be a non-empty string" % key, key)
    return value.strip()


def req_abs_path(params: Dict[str, Any], key: str, must_exist: bool = False) -> str:
    """Paths are always absolute: the engine never resolves paths relative to its own working dir."""
    value = req_str(params, key)
    if not os.path.isabs(value):
        raise ContractError("%s must be an absolute path" % key, key)
    if must_exist and not (os.path.exists(value) or _is_pattern(value)):
        raise ContractError("%s does not exist: %s" % (key, value), key)
    return os.path.normpath(value)


def _is_pattern(path: str) -> bool:
    return any(token in os.path.basename(path) for token in ("#", "%", "$F"))


def opt_int(params: Dict[str, Any], key: str, default: Optional[int], lo: int = -10 ** 7,
            hi: int = 10 ** 7) -> Optional[int]:
    value = params.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ContractError("%s must be an integer in [%d, %d]" % (key, lo, hi), key)
    return value


def opt_bool(params: Dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ContractError("%s must be true or false" % key, key)
    return value


def req_name(params: Dict[str, Any], key: str, default: Optional[str] = None) -> str:
    value = params.get(key, default)
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ContractError("%s must be 1-64 letters, digits, '_' or '-' (starting with a letter or digit)" % key, key)
    return value
