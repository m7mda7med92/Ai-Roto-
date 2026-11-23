"""Rotomation helpers (Phase 04): folders, engine job requests, the fit input file and the maths
that turns a fit or the rig's own joints into bake targets for controls.

No Qt and no Maya imports (Maya data is passed in), so everything here is unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from maya_plugin.communication.contracts import JobRequest, make_job_request
from maya_plugin.modules.body_tracking import retarget as rt
from maya_plugin.modules.body_tracking import rig_mapping as rm
from shared.paths import user_dir

POSE_TRACK_JOB = "pose_track"
ROTO_FIT_JOB = "roto_fit"
POSE_TASKS = ("pose_2d",)
BODY3D_TASK = "body_3d"
DETECTOR_TASK = "person_detection"


def provides(info: Any, task: str) -> bool:
    return info.task == task or task in ((info.options or {}).get("also") or [])


def output_dir(name: str, project: Any = None) -> Path:
    """``<project>/rotomation/<name>`` (or ``<user dir>/rotomation/<name>`` without a project)."""
    base = Path(project.root) / project.paths.get("rotomation", "rotomation") if project is not None \
        else user_dir() / "rotomation"
    return base / name


def rig_preset_dir(project: Any = None) -> Path:
    base = Path(project.root) / project.paths.get("rotomation", "rotomation") if project is not None \
        else user_dir() / "rotomation"
    return base / "rigs"


def pose_file(out_dir: str, name: str) -> Path:
    return Path(out_dir) / (name + ".pose.json")


def fit_file(out_dir: str, name: str) -> Path:
    return Path(out_dir) / (name + ".rotofit.json")


def fit_input_file(out_dir: str, name: str) -> Path:
    return Path(out_dir) / (name + ".fitinput.json")


# ------------------------------------------------------------------------------------------ requests
def pose_request(source: str, first_frame: int, model_id: str, output: str, name: str,
                 body3d_model_id: Optional[str] = None, detector_model_id: Optional[str] = None,
                 subject: Optional[Dict[str, Any]] = None, matte_manifest: Optional[str] = None,
                 start: Optional[int] = None, end: Optional[int] = None, overwrite: bool = False) -> JobRequest:
    params: Dict[str, Any] = {"source": source, "first_frame": first_frame, "model_id": model_id,
                              "output_dir": output, "name": name, "overwrite": overwrite}
    for key, value in (("body3d_model_id", body3d_model_id), ("detector_model_id", detector_model_id),
                       ("subject", subject), ("matte_manifest", matte_manifest), ("start", start), ("end", end)):
        if value not in (None, ""):
            params[key] = value
    return make_job_request(POSE_TRACK_JOB, params, "Body pose '%s'" % name)


def fit_request(pose_path: str, fit_input: str, output: str, name: str, options: Dict[str, Any],
                start: Optional[int] = None, end: Optional[int] = None, overwrite: bool = False) -> JobRequest:
    params: Dict[str, Any] = {"pose_file": pose_path, "fit_input": fit_input, "output_dir": output,
                              "name": name, "options": dict(options), "overwrite": overwrite}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return make_job_request(ROTO_FIT_JOB, params, "Fit '%s' to the Maya camera" % name)


# ------------------------------------------------------------------------------------------ fit input
def build_fit_input(camera: str, samples: Dict[int, Any], rig_name: str, reference_frame: int,
                    joint_positions: Dict[str, Any], up: str, frame_offset: int) -> Dict[str, Any]:
    """The Maya side of the fit: camera per **plate** frame and the rig's joints at the reference frame.

    ``samples`` maps plate frame -> ``maya_guides.CameraSample`` (read at plate frame + offset).
    """
    return {"kind": "roto_fit_input", "version": 1, "up_axis": up, "frame_offset": frame_offset,
            "camera": {"name": camera, "frames": {str(f): asdict(s) for f, s in sorted(samples.items())}},
            "rig": {"name": rig_name, "reference_frame": reference_frame,
                    "joints": {j: [float(v) for v in p] for j, p in joint_positions.items()}}}


def write_json(path: Path, data: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def positions_from_matrices(joints: Dict[str, str], matrices: Dict[str, Dict[int, List[float]]],
                            frame: int) -> Dict[str, Tuple[float, float, float]]:
    """body joint -> world position at ``frame`` from ``read_world_matrices`` output."""
    return {body: tuple(matrices[node][frame][12:15]) for body, node in joints.items()  # type: ignore[misc]
            if node in matrices and frame in matrices[node]}


# ------------------------------------------------------------------------------------------ bake (fit)
def control_offsets(controls: Dict[str, Dict[str, Any]], control_ref: Dict[str, List[float]],
                    rig_ref_positions: Dict[str, Any]) -> Dict[str, Tuple[str, List[float]]]:
    """control -> (driver, offset) recorded at the reference frame (see ``retarget``)."""
    drivers_ref = rt.driver_frames(rig_ref_positions)
    out: Dict[str, Tuple[str, List[float]]] = {}
    for driver, spec in controls.items():
        node = spec.get("node") if isinstance(spec, dict) else None
        if not node or driver not in drivers_ref or node not in control_ref:
            continue
        if driver.endswith("_pole"):
            # pole vectors go straight to the computed pole point (a world-space offset would end up
            # behind the knee once the character turns around)
            out[node] = (driver, list(IDENTITY))
        else:
            out[node] = (driver, rt.offset(control_ref[node], drivers_ref[driver]))
    return out


IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def fit_targets(fit: Dict[str, Any], controls: Dict[str, Dict[str, Any]],
                offsets: Dict[str, Tuple[str, List[float]]], frames: Optional[List[int]] = None
                ) -> Dict[str, Dict[str, Any]]:
    """Bake targets (``maya_bridge.bake_world_targets`` format) for the mapped controls from a fit."""
    wanted = frames if frames is not None else sorted(int(f) for f in fit["frames"])
    per_frame: Dict[int, Dict[str, List[float]]] = {}
    for frame in wanted:
        positions = rt.fit_positions(fit, frame)
        if positions is not None:
            per_frame[frame] = rt.driver_frames(positions)
    by_node = {spec.get("node"): (driver, spec) for driver, spec in controls.items() if isinstance(spec, dict)}
    targets: Dict[str, Dict[str, Any]] = {}
    for node, frames_m in rt.control_targets(offsets, per_frame).items():
        driver, spec = by_node.get(node, (offsets[node][0], {}))
        if frames_m:
            targets[node] = {"frames": frames_m, "channels": rt.channels_for(driver, spec.get("channels"))}
    return targets


def controls_for_mode(controls: Dict[str, Dict[str, Any]], mode: str) -> Dict[str, Dict[str, Any]]:
    """Mapped controls used by a bake mode: "fk", "ik" (IK hands/feet + poles) or "all"."""
    keep = set(rt.FK_DRIVERS if mode == "fk" else rt.IK_DRIVERS if mode == "ik" else rt.DRIVERS)
    return {d: s for d, s in controls.items() if d in keep and isinstance(s, dict) and s.get("node")}


# ------------------------------------------------------------------------------------------ FK / IK match
def record_match(limb: Dict[str, Any], joint_m: Dict[str, List[float]], control_m: Dict[str, List[float]]
                 ) -> Dict[str, Any]:
    """Offsets of the limb's FK / IK controls to the rig joints, taken in a pose where FK and IK agree.

    ``limb`` keys: ``joints`` [upper, mid, end] rig joints, ``fk`` [3 controls], ``ik`` control,
    ``pole`` control. Matrices are world matrices on the match frame.
    """
    joints = limb["joints"]
    offsets: Dict[str, Any] = {"fk": [], "ik": None}
    for joint, control in zip(joints, limb.get("fk") or []):
        if control:
            offsets["fk"].append([control, joint, rt.offset(control_m[control], joint_m[joint])])
    if limb.get("ik"):
        offsets["ik"] = [limb["ik"], joints[2], rt.offset(control_m[limb["ik"]], joint_m[joints[2]])]
    return offsets


def match_targets(limb: Dict[str, Any], direction: str, joint_m: Dict[str, Dict[int, List[float]]],
                  frames: List[int], pole_distance: float = 1.0) -> Dict[str, Dict[str, Any]]:
    """Bake targets that make one side match the other's current pose (read from the rig joints).

    ``direction`` "fk" (FK follows the joints, i.e. IK -> FK) or "ik" (IK + pole follow, FK -> IK).
    """
    offsets = limb.get("offsets") or {}
    joints = limb["joints"]
    targets: Dict[str, Dict[str, Any]] = {}
    if direction == "fk":
        if not offsets.get("fk"):
            raise ValueError("record the match pose first (no FK offsets)")
        for control, joint, off in offsets["fk"]:
            targets[control] = {"frames": {f: rt.mat_mul(off, joint_m[joint][f]) for f in frames}, "channels": "r"}
        return targets
    if not offsets.get("ik"):
        raise ValueError("record the match pose first (no IK offset)")
    control, joint, off = offsets["ik"]
    targets[control] = {"frames": {f: rt.mat_mul(off, joint_m[joint][f]) for f in frames}, "channels": "tr"}
    if limb.get("pole"):
        poles = {}
        for f in frames:
            a, b, c = (rt.translation(joint_m[j][f]) for j in joints)
            normal = rt.unit(rt.cross(rt.sub(b, a), rt.sub(c, b)), (1.0, 0.0, 0.0))
            poles[f] = rt.pole_matrix(a, b, c, normal, pole_distance)
        targets[limb["pole"]] = {"frames": poles, "channels": "t"}
    return targets


def foot_lock_targets(control: str, matrix: List[float], frames: List[int], rotate: bool = True
                      ) -> Dict[str, Dict[str, Any]]:
    """Hold ``control`` at one world matrix over ``frames`` (planted foot)."""
    return {control: {"frames": {f: list(matrix) for f in frames}, "channels": "tr" if rotate else "t"}}


def limb_config(preset: Dict[str, Any], limb: str) -> Dict[str, Any]:
    cfg = dict((preset.get("limbs") or {}).get(limb) or {})
    joints = preset.get("joints") or {}
    cfg["joints"] = [joints.get(j, "") for j in rm.LIMBS[limb]]
    return cfg
