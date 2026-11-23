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

#: Fake scene for query tests: long transform name -> {"kind": "camera"|"locator"|"mesh",
#: "matrix": callable(frame) -> 16 floats, "attrs": {shape attr: value or callable(frame)},
#: "vertices": [(x, y, z), ...]}. Shapes are named "<transform>|<short>Shape".
SCENE: Dict[str, Dict[str, Any]] = {}
SELECTION: List[str] = []
_DEFAULT_CAMERAS = ("persp", "top", "front", "side")


def reset() -> None:
    CALLS.clear()
    _workspace_controls.clear()
    SCENE.clear()
    SELECTION.clear()


def add_node(transform: str, kind: str, matrix: Any, attrs: Any = None, vertices: Any = None) -> str:
    """Register a fake node (test helper, not a Maya command)."""
    SCENE[transform] = {"kind": kind, "matrix": matrix, "attrs": dict(attrs or {}), "vertices": list(vertices or [])}
    return transform


def _shape(transform: str) -> str:
    return "%s|%sShape" % (transform, transform.split("|")[-1])


def _split(name: str) -> Any:
    """``node.attr`` -> (transform, is_shape, attr)."""
    node, _, attr = name.partition(".")
    for transform in SCENE:
        if node == transform:
            return transform, False, attr
        if node == _shape(transform):
            return transform, True, attr
    return None, False, attr


def objExists(name: str) -> bool:  # noqa: N802
    CALLS.append(("objExists", (name,), {}))
    return _split(name)[0] is not None


def listRelatives(node: str, **kwargs: Any) -> Any:  # noqa: N802
    CALLS.append(("listRelatives", (node,), kwargs))
    if kwargs.get("parent"):
        if "|" in node.strip("|"):
            return [node.rsplit("|", 1)[0]]
        short = node.replace("Shape", "")
        return ["|" + short] if short in _DEFAULT_CAMERAS else None
    if kwargs.get("shapes"):
        transform, _is_shape, _attr = _split(node)
        if transform is None:
            return None
        wanted = kwargs.get("type")
        kind = SCENE[transform]["kind"]
        if wanted and wanted != kind:
            return None
        return [_shape(transform)]
    return None


def getAttr(name: str, **kwargs: Any) -> Any:  # noqa: N802
    CALLS.append(("getAttr", (name,), kwargs))
    transform, _is_shape, attr = _split(name)
    if transform is None:
        raise ValueError("No object matches name: %s" % name)
    node = SCENE[transform]
    frame = kwargs.get("time", 1.0)
    if attr == "worldMatrix[0]":
        return list(node["matrix"](frame))
    value = node["attrs"][attr]
    return value(frame) if callable(value) else value


def xform(name: str, **kwargs: Any) -> Any:
    CALLS.append(("xform", (name,), kwargs))
    if _is_query(kwargs) and name.endswith(".vtx[*]"):
        transform, _is_shape, _attr = _split(name.split(".")[0])
        return [c for v in SCENE[transform]["vertices"] for c in v]
    return None


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
    if kwargs.get("selection"):
        return list(SELECTION)
    if kwargs.get("type") == "camera":
        extra = [_shape(t) for t, n in SCENE.items() if n["kind"] == "camera"]
        if kwargs.get("long"):
            return ["|%s|%sShape" % (c, c) for c in _DEFAULT_CAMERAS] + extra
        return ["%sShape" % c for c in _DEFAULT_CAMERAS] + [e.split("|")[-1] for e in extra]
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
