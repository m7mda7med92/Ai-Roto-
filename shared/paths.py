"""Per-user directories shared by the plugin, the engine and the scripts.

Nothing here is machine-specific: every location is derived from the
environment at runtime and can be overridden with ``MAIT_USER_DIR``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from shared.constants.engine_constants import ENV_SESSION_FILE, ENV_USER_DIR, SESSION_FILE_NAME

_APP_DIR_NAME = "MayaAIToolkit"


def toolkit_root() -> Path:
    """Root folder of the toolkit checkout (the folder that contains ``shared/``)."""
    return Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """Per-user data folder (settings, logs, runtime files). Created on demand by callers."""
    override = os.environ.get(ENV_USER_DIR)
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / _APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _APP_DIR_NAME


def logs_dir() -> Path:
    return user_dir() / "logs"


def runtime_dir() -> Path:
    return user_dir() / "runtime"


def session_file() -> Path:
    """File the engine writes on startup: pid, host, port and session token."""
    override = os.environ.get(ENV_SESSION_FILE)
    if override:
        return Path(override).expanduser()
    return runtime_dir() / SESSION_FILE_NAME


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
