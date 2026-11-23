"""Layered settings: ``config/default_settings.json`` <- ``<user dir>/user_settings.json``.

Only the *user overrides* are ever written to disk, so upgrading the toolkit
picks up new defaults automatically and the shipped defaults file is never
modified. Keys are addressed with dotted paths, e.g. ``"engine.port"``.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from maya_plugin.core.logger import get_logger
from shared.paths import toolkit_root, user_dir

log = get_logger("config")

_MISSING = object()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_json_object(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable settings file", extra={"path": str(path), "error": str(exc)})
        return {}
    if not isinstance(data, dict):
        log.warning("Ignoring settings file that is not an object", extra={"path": str(path)})
        return {}
    data.pop("_comment", None)
    return data


class ConfigManager:
    def __init__(self, defaults_path: Optional[Path] = None, user_path: Optional[Path] = None) -> None:
        self.defaults_path = defaults_path or toolkit_root() / "config" / "default_settings.json"
        self.user_path = user_path or user_dir() / "user_settings.json"
        self._lock = threading.RLock()
        self._defaults: Dict[str, Any] = {}
        self._user: Dict[str, Any] = {}
        self._merged: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._defaults = _read_json_object(self.defaults_path)
            self._user = _read_json_object(self.user_path)
            self._merged = _deep_merge(self._defaults, self._user)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._merged
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        """Set a user override (and persist it unless ``save=False``)."""
        parts = key.split(".")
        with self._lock:
            node = self._user
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node[parts[-1]] = copy.deepcopy(value)
            self._merged = _deep_merge(self._defaults, self._user)
            if save:
                self.save()

    def reset(self, key: str, save: bool = True) -> None:
        """Remove a user override so the default applies again."""
        parts = key.split(".")
        with self._lock:
            node: Any = self._user
            for part in parts[:-1]:
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    return
            if isinstance(node, dict):
                node.pop(parts[-1], None)
            self._merged = _deep_merge(self._defaults, self._user)
            if save:
                self.save()

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._merged)

    def save(self) -> bool:
        """Atomically write user overrides. Returns False (and logs) on failure - never raises."""
        with self._lock:
            payload = json.dumps(self._user, indent=2, sort_keys=True)
        try:
            self.user_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=str(self.user_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, str(self.user_path))
            return True
        except OSError as exc:
            log.error("Could not save settings", extra={"path": str(self.user_path), "error": str(exc)})
            return False
