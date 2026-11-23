"""FAKE ``maya.cmds`` that records every call.

``CALLS`` holds ``(command, args, kwargs)`` tuples so tests can assert that the
plugin never issued a scene-modifying command.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

CALLS: List[Tuple[str, tuple, Dict[str, Any]]] = []

#: Commands that never modify the scene (UI or pure queries).
UI_COMMANDS = {"workspaceControl", "deleteUI", "warning", "shelfLayout", "shelfButton", "setParent",
               "layout", "control", "window", "evalDeferred", "inViewMessage"}
QUERY_ONLY_COMMANDS = {"about", "ls", "objExists", "nodeType", "listRelatives", "getAttr"}

_workspace_controls: set = set()


def reset() -> None:
    CALLS.clear()
    _workspace_controls.clear()


def _is_query(kwargs: Dict[str, Any]) -> bool:
    return bool(kwargs.get("query") or kwargs.get("q"))


def scene_modifying_calls() -> List[Tuple[str, tuple, Dict[str, Any]]]:
    """Calls that are neither UI commands, query-only commands, nor made with ``query=True``."""
    result = []
    for name, args, kwargs in CALLS:
        if name in UI_COMMANDS or name in QUERY_ONLY_COMMANDS or _is_query(kwargs):
            continue
        result.append((name, args, kwargs))
    return result


def about(*args: Any, **kwargs: Any) -> Any:
    CALLS.append(("about", args, kwargs))
    if kwargs.get("version"):
        return "2024"
    if kwargs.get("apiVersion"):
        return 20240200
    if kwargs.get("batch"):
        return False
    return ""


def workspaceControl(name: str, **kwargs: Any) -> Any:  # noqa: N802
    CALLS.append(("workspaceControl", (name,), kwargs))
    if kwargs.get("exists"):
        return name in _workspace_controls
    if kwargs.get("close"):
        return None
    _workspace_controls.add(name)
    return name


def deleteUI(name: str, **kwargs: Any) -> None:  # noqa: N802
    CALLS.append(("deleteUI", (name,), kwargs))
    _workspace_controls.discard(name)


def file(*args: Any, **kwargs: Any) -> Any:
    CALLS.append(("file", args, kwargs))
    if _is_query(kwargs):
        if kwargs.get("sceneName"):
            return ""
        if kwargs.get("modified"):
            return False
    return None


def ls(*args: Any, **kwargs: Any) -> List[str]:
    CALLS.append(("ls", args, kwargs))
    if kwargs.get("type") == "camera":
        return ["perspShape", "topShape", "frontShape", "sideShape"]
    return []


def currentUnit(**kwargs: Any) -> str:  # noqa: N802
    CALLS.append(("currentUnit", (), kwargs))
    return "film" if kwargs.get("time") else "cm"


def playbackOptions(**kwargs: Any) -> float:  # noqa: N802
    CALLS.append(("playbackOptions", (), kwargs))
    return 1.0 if kwargs.get("minTime") else 120.0


def warning(message: str) -> None:
    CALLS.append(("warning", (message,), {}))


def __getattr__(name: str) -> Any:
    """Any other command is recorded and treated as potentially scene-modifying."""
    def command(*args: Any, **kwargs: Any) -> None:
        CALLS.append((name, args, kwargs))
    return command
