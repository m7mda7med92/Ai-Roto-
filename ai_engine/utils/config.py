"""Engine settings: built-in defaults <- config/engine_settings.json <- CLI overrides."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

from shared.constants.engine_constants import ALLOWED_BIND_HOSTS, DEFAULT_ENGINE_HOST
from shared.contracts.serialization import loads
from shared.paths import toolkit_root

DEFAULT_ENGINE_SETTINGS: Dict[str, Any] = {
    "server": {
        "host": DEFAULT_ENGINE_HOST,
        "port": 0,                       # 0 = let the OS pick a free port (advertised in the session file)
        "max_request_bytes": 1048576,
        "request_timeout_seconds": 30,
    },
    "jobs": {
        "max_concurrent_jobs": 2,
        "max_queued_jobs": 100,
        "history_limit": 200,
    },
    "gpu": {
        "detect_with_nvidia_smi": True,
        "detect_with_torch": True,
        "nvidia_smi_timeout_seconds": 5,
    },
    "logging": {
        "level": "INFO",
        "json_file": True,
        "max_bytes": 5 * 1024 * 1024,
        "backup_count": 3,
    },
    "watchdog": {
        "parent_check_interval_seconds": 2.0,
    },
    "simulated_job": {
        "default_duration_seconds": 10.0,
        "default_steps": 50,
        "max_duration_seconds": 3600.0,
    },
}


class ConfigError(ValueError):
    """Raised for invalid engine configuration."""


def default_config_path() -> Path:
    return toolkit_root() / "config" / "engine_settings.json"


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``base`` recursively updated with ``override``."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_engine_settings(path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load and validate engine settings. Missing file -> defaults."""
    settings = copy.deepcopy(DEFAULT_ENGINE_SETTINGS)
    config_path = Path(path) if path else default_config_path()
    if config_path.exists():
        try:
            data = loads(config_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError("invalid JSON in %s: %s" % (config_path, exc))
        if not isinstance(data, dict):
            raise ConfigError("%s must contain a JSON object" % config_path)
        data.pop("_comment", None)
        settings = deep_merge(settings, data)
    elif path is not None:
        raise ConfigError("engine settings file not found: %s" % config_path)
    if overrides:
        settings = deep_merge(settings, overrides)
    validate_engine_settings(settings)
    return settings


def validate_engine_settings(settings: Dict[str, Any]) -> None:
    server = settings["server"]
    if server.get("host") not in ALLOWED_BIND_HOSTS:
        raise ConfigError("server.host must be 127.0.0.1 (got %r); the engine never binds to other interfaces"
                          % server.get("host"))
    port = server.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port < 65536:
        raise ConfigError("server.port must be an integer in [0, 65535]")
    jobs = settings["jobs"]
    for key in ("max_concurrent_jobs", "max_queued_jobs", "history_limit"):
        value = jobs.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError("jobs.%s must be a positive integer" % key)
