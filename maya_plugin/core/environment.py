"""Runtime environment detection (OS, Python, Maya, Qt).

Every probe is defensive: this module works outside Maya (tests, diagnostics)
and only *reads* information. GPU capabilities are reported by the engine
(``/health``) because the Maya process must not load CUDA libraries.
"""
from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from shared.version import MIN_PLUGIN_PYTHON


@dataclass
class EnvironmentInfo:
    os_name: str
    os_release: str
    python_version: str
    python_executable: str
    in_maya: bool = False
    maya_version: Optional[str] = None
    maya_api_version: Optional[int] = None
    maya_batch: Optional[bool] = None
    qt_binding: Optional[str] = None
    qt_binding_version: Optional[str] = None
    qt_version: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_maya() -> Dict[str, Any]:
    """Return Maya info if running inside Maya, else ``{"in_maya": False}``. Read-only."""
    try:
        from maya import cmds  # type: ignore
    except Exception:
        return {"in_maya": False}
    info: Dict[str, Any] = {"in_maya": True}
    try:
        info["maya_version"] = str(cmds.about(version=True))
        info["maya_api_version"] = int(cmds.about(apiVersion=True))
        info["maya_batch"] = bool(cmds.about(batch=True))
    except Exception:
        pass
    return info


def detect_qt() -> Dict[str, Any]:
    """Detect the Qt binding the UI will use, without importing it if Qt is absent."""
    try:
        from maya_plugin.ui.qt import QT_BINDING, QT_BINDING_VERSION, QtCore
    except Exception:
        return {}
    return {"qt_binding": QT_BINDING, "qt_binding_version": QT_BINDING_VERSION, "qt_version": QtCore.qVersion()}


def detect_environment(include_qt: bool = True) -> EnvironmentInfo:
    info = EnvironmentInfo(os_name=platform.system(), os_release=platform.release(),
                           python_version=platform.python_version(), python_executable=sys.executable)
    for key, value in detect_maya().items():
        setattr(info, key, value)
    if include_qt:
        for key, value in detect_qt().items():
            setattr(info, key, value)
    if sys.version_info[:2] < MIN_PLUGIN_PYTHON:
        info.warnings.append("Python %s is older than the supported %d.%d" %
                             ((info.python_version,) + MIN_PLUGIN_PYTHON))
    if info.in_maya and info.maya_version:
        try:
            if int(str(info.maya_version)[:4]) < 2024:
                info.warnings.append("Maya %s is older than the supported 2024" % info.maya_version)
        except ValueError:
            pass
    if info.os_name != "Windows":
        info.warnings.append("Primary target is Windows; %s is best-effort" % info.os_name)
    return info
