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

    cameras = read_camera_samples(camera, frames)
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


# ------------------------------------------------------------------------------------ Phase 04
# Rotomation. Reads (camera, rig joints and controls) are query-only, like Phase 03. Writes happen
# only from explicit buttons, through run_scene_operation (main thread, one undo step).

#: centimetres (Maya's internal unit, used by matrix attributes) -> one unit of each UI linear unit
_CM_PER_UNIT = {"mm": 0.1, "millimeter": 0.1, "cm": 1.0, "centimeter": 1.0, "m": 100.0, "meter": 100.0,
                "km": 100000.0, "kilometer": 100000.0, "in": 2.54, "inch": 2.54, "ft": 30.48, "foot": 30.48,
                "yd": 91.44, "yard": 91.44, "mi": 160934.4, "mile": 160934.4}


def internal_to_ui_scale() -> float:
    """Factor from matrix-attribute units (cm) to the scene's UI linear unit (translate values)."""
    from maya import cmds  # type: ignore
    unit = str(cmds.currentUnit(query=True, linear=True))
    return 1.0 / _CM_PER_UNIT.get(unit, 1.0)


def up_axis() -> str:
    from maya import cmds  # type: ignore
    try:
        return "z" if str(cmds.upAxis(query=True, axis=True)).lower() == "z" else "y"
    except Exception:  # pragma: no cover - very old Maya
        return "y"


def read_camera_samples(camera: str, frames: list) -> dict:
    """Maya frame -> ``maya_guides.CameraSample`` for the tracked camera (query only)."""
    from maya import cmds  # type: ignore
    from maya_plugin.modules.roto_studio import maya_guides as mg

    if not cmds.objExists(camera):
        raise ValueError("camera %s does not exist" % camera)
    shapes = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True) or []
    if not shapes:
        raise ValueError("%s is not a camera" % camera)
    shape = shapes[0]
    fixed = {name: _scalar(cmds.getAttr("%s.%s" % (shape, name))) for name in
             ("horizontalFilmAperture", "verticalFilmAperture", "filmFit", "lensSqueezeRatio",
              "horizontalFilmOffset", "verticalFilmOffset")}
    return {int(frame): mg.CameraSample(
        world_matrix=_matrix_at(camera, frame), focal_mm=_scalar(cmds.getAttr(shape + ".focalLength", time=frame)),
        h_aperture_in=fixed["horizontalFilmAperture"], v_aperture_in=fixed["verticalFilmAperture"],
        film_fit=int(fixed["filmFit"]), h_offset_in=fixed["horizontalFilmOffset"],
        v_offset_in=fixed["verticalFilmOffset"], lens_squeeze=fixed["lensSqueezeRatio"]) for frame in frames}


def read_world_matrices(nodes: list, frames: list) -> dict:
    """node -> {frame: world matrix (16 floats, cm)} without changing the current time (query only)."""
    from maya import cmds  # type: ignore
    out = {}
    for node in nodes:
        if not cmds.objExists(node):
            raise ValueError("%s does not exist in the scene" % node)
        out[node] = {int(frame): _matrix_at(node, frame) for frame in frames}
    return out


def joint_hierarchy(root: str) -> list:
    """``root`` and every joint/transform under it, parents first (for the auto mapping)."""
    from maya import cmds  # type: ignore
    below = cmds.listRelatives(root, allDescendents=True, fullPath=True, type="joint") or []
    return [root] + sorted(below, key=lambda n: (n.count("|"), n))


def keyable_channels(node: str, channels: str) -> dict:
    """{'t': [axes], 'r': [axes]} of translate/rotate axes that are unlocked, keyable and not driven."""
    from maya import cmds  # type: ignore
    out: dict = {"t": [], "r": []}
    for kind, attr in (("t", "translate"), ("r", "rotate")):
        if kind not in channels:
            continue
        for axis in "XYZ":
            plug = "%s.%s%s" % (node, attr, axis)
            try:
                locked = cmds.getAttr(plug, lock=True)
                keyable = cmds.getAttr(plug, keyable=True)
            except Exception:
                continue
            driven = cmds.listConnections(plug, source=True, destination=False, skipConversionNodes=True,
                                          type="constraint")
            if not locked and keyable and not driven:
                out[kind].append(axis.lower())
    return out


def _restore_time(value: float) -> None:
    from maya import cmds  # type: ignore
    try:
        cmds.currentTime(value, edit=True, update=True)
    except Exception:  # pragma: no cover
        pass


def bake_world_targets(targets: dict, frames: list, label: str) -> dict:
    """Make controls follow world matrices and bake them to keys. **Changes the scene** (one undo step).

    ``targets``: control -> {"frames": {frame: 16-float world matrix (cm)}, "channels": "t" | "r" | "tr"}.
    Temporary locators are keyed at the targets, the controls are constrained to them (unlocked,
    keyable axes only, no offset), baked over ``frames`` and the helpers are deleted again.
    """
    from maya_plugin.modules.body_tracking import retarget as rt

    def operation() -> dict:
        from maya import cmds  # type: ignore
        to_ui = internal_to_ui_scale()
        frames_sorted = sorted(int(f) for f in frames)
        start_time = cmds.currentTime(query=True)
        group = cmds.group(empty=True, name="aiRoto_bake_tmp#")
        constraints, baked, skipped = [], [], {}
        try:
            for control, spec in targets.items():
                channels = keyable_channels(control, spec.get("channels", "tr"))
                if not channels["t"] and not channels["r"]:
                    skipped[control] = "no unlocked translate/rotate channels"
                    continue
                locator = cmds.spaceLocator(name="aiRoto_target#")[0]
                locator = cmds.parent(locator, group)[0]
                previous = None
                for frame in frames_sorted:
                    matrix = spec["frames"].get(frame)
                    if matrix is None:
                        continue
                    rotation = rt.unwrap(previous, rt.euler_xyz(matrix))
                    previous = rotation
                    values = {"tx": matrix[12] * to_ui, "ty": matrix[13] * to_ui, "tz": matrix[14] * to_ui,
                              "rx": rotation[0], "ry": rotation[1], "rz": rotation[2]}
                    for attr, value in values.items():
                        cmds.setKeyframe(locator, attribute=attr, time=frame, value=value)
                skip_t = [a for a in "xyz" if a not in channels["t"]]
                skip_r = [a for a in "xyz" if a not in channels["r"]]
                kwargs = {"maintainOffset": False}
                if skip_t:
                    kwargs["skipTranslate"] = skip_t
                if skip_r:
                    kwargs["skipRotate"] = skip_r
                constraints.extend(cmds.parentConstraint(locator, control, **kwargs) or [])
                baked.append((control, channels))
            if baked:
                attributes = sorted({"%s%s" % (k, a) for _c, ch in baked for k in ("t", "r") for a in ch[k]})
                cmds.bakeResults([c for c, _ in baked], time=(frames_sorted[0], frames_sorted[-1]),
                                 simulation=True, attribute=attributes, preserveOutsideKeys=True,
                                 sampleBy=1, disableImplicitControl=True, minimizeRotation=True)
        finally:
            if constraints:
                cmds.delete(constraints)
            cmds.delete(group)
            _restore_time(start_time)
        return {"baked": [c for c, _ in baked], "skipped": skipped, "frames": len(frames_sorted)}

    return run_scene_operation(label, operation)


def create_solve_skeleton(name: str, joints: list, positions: dict) -> str:
    """Keyed locators at the fitted joints, under ``<name>_solve_grp``. **Changes the scene** (one undo step).

    ``positions``: frame -> list of world positions (cm), in ``joints`` order.
    """
    def operation() -> str:
        from maya import cmds  # type: ignore
        to_ui = internal_to_ui_scale()
        group = cmds.group(empty=True, name="%s_solve_grp" % name)
        for index, joint in enumerate(joints):
            locator = cmds.spaceLocator(name="%s_%s_loc" % (name, joint))[0]
            locator = cmds.parent(locator, group)[0]
            for frame in sorted(positions):
                point = positions[frame][index]
                for attr, value in zip(("tx", "ty", "tz"), point):
                    cmds.setKeyframe(locator, attribute=attr, time=frame, value=float(value) * to_ui)
        return group

    return run_scene_operation("AI Roto: create solve skeleton", operation)


def key_attribute(plug: str, frames: list, value: float, label: str) -> None:
    """Key ``plug`` to ``value`` on ``frames`` (the IK/FK switch after a match). **Changes the scene**."""
    def operation() -> None:
        from maya import cmds  # type: ignore
        for frame in frames:
            cmds.setKeyframe(plug, time=frame, value=value)
    run_scene_operation(label, operation)


def current_frame() -> float:
    from maya import cmds  # type: ignore
    return float(cmds.currentTime(query=True))
