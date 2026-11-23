"""Toolkit projects: a folder on disk with a ``mait_project.json`` manifest.

A toolkit project is independent of the Maya scene: creating, opening or
browsing projects only touches the project folder and the user settings file,
never the open Maya scene.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.logger import get_logger
from shared.version import TOOLKIT_VERSION

log = get_logger("projects")

MANIFEST_NAME = "mait_project.json"
DEFAULT_SUBFOLDERS = ("footage", "shots", "tracking", "roto", "depth", "workflows", "exports", "cache")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,63}$")


class ProjectError(Exception):
    pass


@dataclass
class Project:
    name: str
    root: str
    created_at: float
    toolkit_version: str = TOOLKIT_VERSION
    paths: Dict[str, str] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    def shots(self) -> List[str]:
        shots_dir = self.root_path / self.paths.get("shots", "shots")
        try:
            return sorted(p.name for p in shots_dir.iterdir() if p.is_dir())
        except OSError:
            return []

    def to_manifest(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("root")  # the manifest is relocatable
        return data


class ProjectService:
    def __init__(self, config: ConfigManager, events: EventManager) -> None:
        self._config = config
        self._events = events
        self.current: Optional[Project] = None

    def create_project(self, parent_dir: str, name: str) -> Project:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise ProjectError("Project name must be 1-64 characters: letters, digits, space, '_' or '-'.")
        parent = Path(parent_dir).expanduser()
        if not parent.is_dir():
            raise ProjectError("Parent folder does not exist: %s" % parent)
        root = parent / name
        if (root / MANIFEST_NAME).exists():
            raise ProjectError("A toolkit project already exists at %s" % root)
        if root.exists() and any(root.iterdir()):
            raise ProjectError("Folder %s exists and is not empty" % root)
        try:
            for sub in DEFAULT_SUBFOLDERS:
                (root / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectError("Could not create project folders: %s" % exc)
        project = Project(name=name, root=str(root), created_at=time.time(),
                          paths={sub: sub for sub in DEFAULT_SUBFOLDERS})
        self._write_manifest(project)
        log.info("Project created", extra={"project": name, "root": str(root)})
        return self._activate(project)

    def open_project(self, path: str) -> Project:
        root = Path(path).expanduser()
        if root.is_file() and root.name == MANIFEST_NAME:
            root = root.parent
        manifest = root / MANIFEST_NAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProjectError("No %s found in %s" % (MANIFEST_NAME, root))
        except (OSError, ValueError) as exc:
            raise ProjectError("Cannot read project manifest: %s" % exc)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ProjectError("Invalid project manifest")
        project = Project(name=data["name"], root=str(root), created_at=float(data.get("created_at", 0)),
                          toolkit_version=str(data.get("toolkit_version", "")),
                          paths=dict(data.get("paths") or {}), settings=dict(data.get("settings") or {}))
        log.info("Project opened", extra={"project": project.name, "root": str(root)})
        return self._activate(project)

    def save_current(self) -> bool:
        """Persist the current project's manifest (toolkit data only - never the Maya scene)."""
        if self.current is None:
            return False
        self._write_manifest(self.current)
        return True

    def close_project(self) -> None:
        self.current = None
        self._events.publish(Events.PROJECT_CHANGED, None)

    def recent_projects(self) -> List[str]:
        recent = self._config.get("projects.recent", []) or []
        return [p for p in recent if isinstance(p, str)]

    # ------------------------------------------------------------------ internals
    def _activate(self, project: Project) -> Project:
        self.current = project
        recent = [p for p in self.recent_projects() if os.path.normcase(p) != os.path.normcase(project.root)]
        recent.insert(0, project.root)
        self._config.set("projects.recent", recent[: int(self._config.get("projects.max_recent", 10))])
        self._events.publish(Events.PROJECT_CHANGED, project)
        return project

    @staticmethod
    def _write_manifest(project: Project) -> None:
        root = project.root_path
        fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=str(root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(project.to_manifest(), handle, indent=2)
            os.replace(tmp, str(root / MANIFEST_NAME))
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise ProjectError("Could not write project manifest: %s" % exc)
