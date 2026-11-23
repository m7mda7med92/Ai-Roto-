"""The only module that talks to the Maya scene API.

Rules enforced here (see docs/ARCHITECTURE.md, "Scene safety"):

* Read functions use query-only commands and never change the scene.
* Scene-changing operations (future phases) must go through
  :func:`run_scene_operation`, which runs on Maya's main thread and wraps the
  work in a single undo chunk so the user can undo it in one step.
* Nothing here is ever called from a background thread directly;
  :func:`run_in_main_thread` marshals calls when needed.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Callable, Dict, Iterator, TypeVar

T = TypeVar("T")


def is_maya_available() -> bool:
    try:
        import maya.cmds  # type: ignore
    except Exception:
        return False
    return hasattr(maya.cmds, "about")


def run_in_main_thread(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn`` on Maya's main thread (directly if already there)."""
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)
    import maya.utils  # type: ignore
    return maya.utils.executeInMainThreadWithResult(fn, *args, **kwargs)


@contextlib.contextmanager
def undo_chunk(name: str) -> Iterator[None]:
    """Group all scene edits inside the block into one undoable step."""
    from maya import cmds  # type: ignore
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def run_scene_operation(name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Entry point for *explicit, user-initiated* scene changes (future phases)."""
    def wrapped() -> T:
        with undo_chunk(name):
            return fn(*args, **kwargs)
    return run_in_main_thread(wrapped)


def read_scene_info() -> Dict[str, Any]:
    """Query-only snapshot of the open scene. Must be called on the main thread."""
    from maya import cmds  # type: ignore
    scene = cmds.file(query=True, sceneName=True) or "(untitled)"
    return {
        "scene": scene,
        "modified": bool(cmds.file(query=True, modified=True)),
        "maya_version": cmds.about(version=True),
        "linear_unit": cmds.currentUnit(query=True, linear=True),
        "time_unit": cmds.currentUnit(query=True, time=True),
        "frame_range": (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True)),
        "cameras": sorted(cmds.ls(type="camera", long=False) or []),
        "meshes": len(cmds.ls(type="mesh", noIntermediate=True) or []),
        "joints": len(cmds.ls(type="joint") or []),
        "selection": len(cmds.ls(selection=True) or []),
    }
