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


# ------------------------------------------------------------------------------------ Phase 03
# Query-only reads of the artist's own tracking (camera + locators / geometry) for guided roto.
# Every command here is a query (``ls``, ``listRelatives``, ``getAttr``, ``xform(query=True)``),
# so reading guides never changes the scene, the current time or the selection.

def list_cameras() -> list:
    """Camera transforms in the scene (long names), user cameras first."""
    from maya import cmds  # type: ignore
    defaults = {"persp", "top", "front", "side"}
    transforms = []
    for shape in cmds.ls(type="camera", long=True) or []:
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parents and parents[0] not in transforms:
            transforms.append(parents[0])
    return sorted(transforms, key=lambda t: (t.split("|")[-1] in defaults, t))


def selected_transforms() -> list:
    from maya import cmds  # type: ignore
    return list(cmds.ls(selection=True, type="transform", long=True) or [])


def _matrix_at(node: str, frame: float) -> list:
    from maya import cmds  # type: ignore
    return [float(v) for v in cmds.getAttr(node + ".worldMatrix[0]", time=frame)]


def _scalar(value: Any) -> float:
    while isinstance(value, (list, tuple)):  # getAttr may wrap values in lists/tuples
        value = value[0]
    return float(value)


def read_guide_scene(camera: str, includes: list, excludes: list, frames: list,
                     max_vertices: int = 400) -> Any:
    """Sample the camera and guide objects at ``frames`` (Maya frame numbers). Main thread only."""
    from maya import cmds  # type: ignore
    from maya_plugin.modules.roto_studio import maya_guides as mg

    if not cmds.objExists(camera):
        raise ValueError("camera %s does not exist" % camera)
    shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
    if not shapes:
        raise ValueError("%s is not a camera" % camera)
    shape = shapes[0]
    fixed = {name: cmds.getAttr("%s.%s" % (shape, name)) for name in
             ("horizontalFilmAperture", "verticalFilmAperture", "filmFit", "lensSqueezeRatio",
              "horizontalFilmOffset", "verticalFilmOffset")}
    cameras = {}
    for frame in frames:
        cameras[int(frame)] = mg.CameraSample(
            world_matrix=_matrix_at(camera, frame),
            focal_mm=_scalar(cmds.getAttr(shape + ".focalLength", time=frame)),
            h_aperture_in=_scalar(fixed["horizontalFilmAperture"]),
            v_aperture_in=_scalar(fixed["verticalFilmAperture"]),
            film_fit=int(_scalar(fixed["filmFit"])), h_offset_in=_scalar(fixed["horizontalFilmOffset"]),
            v_offset_in=_scalar(fixed["verticalFilmOffset"]), lens_squeeze=_scalar(fixed["lensSqueezeRatio"]))
    objects = []
    for role, nodes in ((mg.INCLUDE, includes), (mg.EXCLUDE, excludes)):
        for node in nodes:
            if not cmds.objExists(node):
                raise ValueError("%s no longer exists in the scene" % node)
            meshes = cmds.listRelatives(node, shapes=True, type="mesh", noIntermediate=True, fullPath=True) or []
            if meshes:
                flat = cmds.xform(meshes[0] + ".vtx[*]", query=True, translation=True, objectSpace=True) or []
                local = mg.sample_points([flat[i:i + 3] for i in range(0, len(flat), 3)], max_vertices)
                kind = "mesh"
            else:
                local, kind = [(0.0, 0.0, 0.0)], "point"
            matrices = {int(frame): _matrix_at(node, frame) for frame in frames}
            objects.append(mg.GuideObject(node, role, kind, local, matrices))
    return mg.GuideScene(camera, cameras, objects)
