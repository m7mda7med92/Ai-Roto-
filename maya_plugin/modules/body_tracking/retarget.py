"""Turn fitted joint positions into targets for the artist's rig controls (Phase 04).

The fit gives world positions of the body joints. From any set of positions this module builds a
*driver frame* (a 4x4 world matrix) for each body part, the same way for the rig's own joints at
the reference frame and for the fitted joints on every frame:

* pelvis / chest: left-right line (hips / shoulders) and the pelvis -> neck line;
* neck, head: the neck -> head line, turned with the chest;
* arm / leg segments: aim along the bone, bend-plane normal from the three joints of the limb
  (sign kept consistent with the body's left axis, so a straight limb does not flip);
* hands / feet: forearm / foot direction (ankle -> toe) in the limb's bend plane;
* poles: a point in front of the elbow / knee in the bend plane (translation only).

A control keeps its offset to its driver frame from the reference frame::

    offset = control_world(ref) * inverse(driver(ref))      (Maya row-vector order)
    control_world(f) = offset * driver(f)

so any rig works without a T-pose, as long as the reference frame shows the rig in the same pose
as its joints (the controls drive the joints). Pure Python (lists), no Maya, no numpy.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from maya_plugin.modules.roto_studio.maya_guides import mat_inverse

Vec = Tuple[float, float, float]
Matrix = List[float]

#: driver -> (label, default channels "t" / "r" / "tr")
DRIVERS: Dict[str, Tuple[str, str]] = {
    "pelvis": ("Hips / COG control", "tr"),
    "chest": ("Chest control", "r"),
    "neck": ("Neck control", "r"),
    "head": ("Head control", "r"),
    "l_upperarm": ("L arm FK upper", "r"), "l_forearm": ("L arm FK lower", "r"), "l_hand": ("L hand FK", "r"),
    "r_upperarm": ("R arm FK upper", "r"), "r_forearm": ("R arm FK lower", "r"), "r_hand": ("R hand FK", "r"),
    "l_hand_ik": ("L hand IK control", "tr"), "r_hand_ik": ("R hand IK control", "tr"),
    "l_arm_pole": ("L elbow pole vector", "t"), "r_arm_pole": ("R elbow pole vector", "t"),
    "l_thigh": ("L leg FK upper", "r"), "l_shin": ("L leg FK lower", "r"), "l_foot": ("L foot FK", "r"),
    "r_thigh": ("R leg FK upper", "r"), "r_shin": ("R leg FK lower", "r"), "r_foot": ("R foot FK", "r"),
    "l_foot_ik": ("L foot IK control", "tr"), "r_foot_ik": ("R foot IK control", "tr"),
    "l_leg_pole": ("L knee pole vector", "t"), "r_leg_pole": ("R knee pole vector", "t"),
    "l_toe": ("L toe control", "r"), "r_toe": ("R toe control", "r"),
}
FK_DRIVERS = ["pelvis", "chest", "neck", "head", "l_upperarm", "l_forearm", "l_hand", "r_upperarm", "r_forearm",
              "r_hand", "l_thigh", "l_shin", "l_foot", "r_thigh", "r_shin", "r_foot", "l_toe", "r_toe"]
IK_DRIVERS = ["pelvis", "chest", "neck", "head", "l_hand_ik", "r_hand_ik", "l_arm_pole", "r_arm_pole",
              "l_foot_ik", "r_foot_ik", "l_leg_pole", "r_leg_pole", "l_toe", "r_toe"]


# ------------------------------------------------------------------------------------------ vectors
def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Sequence[float], s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Sequence[float]) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Sequence[float], fallback: Sequence[float] = (0.0, 1.0, 0.0)) -> Vec:
    n = length(a)
    return tuple(v / n for v in a) if n > 1e-9 else tuple(fallback)  # type: ignore[return-value]


def mat_mul(a: Sequence[float], b: Sequence[float]) -> Matrix:
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def frame_matrix(x: Vec, y: Vec, z: Vec, origin: Sequence[float]) -> Matrix:
    return [x[0], x[1], x[2], 0.0, y[0], y[1], y[2], 0.0, z[0], z[1], z[2], 0.0,
            float(origin[0]), float(origin[1]), float(origin[2]), 1.0]


def translation(m: Sequence[float]) -> Vec:
    return (m[12], m[13], m[14])


def orthonormal(aim_y: Vec, side_x: Vec) -> Tuple[Vec, Vec, Vec]:
    """Frame with y along ``aim_y`` and x as close as possible to ``side_x``."""
    y = unit(aim_y)
    x = sub(side_x, scale(y, dot(side_x, y)))
    if length(x) < 1e-9:  # side line parallel to the aim: pick any perpendicular
        x = cross(y, (0.0, 0.0, 1.0)) if length(cross(y, (0.0, 0.0, 1.0))) > 1e-6 else (1.0, 0.0, 0.0)
    x = unit(x)
    z = cross(x, y)
    return x, y, z


# ------------------------------------------------------------------------------------------ frames
def _limb_normal(a: Vec, b: Vec, c: Vec, reference: Vec) -> Vec:
    """Bend-plane normal of a 3-joint limb, signed to agree with ``reference`` and blended towards it
    when the limb is nearly straight (so the twist stays stable)."""
    upper, lower = sub(b, a), sub(c, b)
    n = cross(upper, lower)
    size = length(upper) * length(lower)
    n = scale(n, 1.0 / size) if size > 1e-12 else (0.0, 0.0, 0.0)  # |n| = sin(bend angle)
    if dot(n, reference) < 0:
        n = scale(n, -1.0)
    weight = max(0.0, 0.17 - length(n)) / 0.17  # fully the reference below ~10 degrees of bend
    return unit(add(scale(unit(n, reference), 1.0 - weight), scale(reference, weight)), reference)


def _segment(origin: Vec, aim: Vec, normal: Vec) -> Matrix:
    """y along the bone, z = bend normal, x = y x z (points to where the joint bends)."""
    y = unit(aim)
    z = unit(sub(normal, scale(y, dot(normal, y))), normal)
    x = cross(y, z)
    return frame_matrix(x, y, z, origin)


def driver_frames(p: Dict[str, Sequence[float]], pole_distance: float = 1.0) -> Dict[str, Matrix]:
    """World matrices of all drivers that ``p`` (body joint -> world position) supports."""
    P = {k: (float(v[0]), float(v[1]), float(v[2])) for k, v in p.items()}
    out: Dict[str, Matrix] = {}
    pelvis, neck = P["pelvis"], P["neck"]
    up = sub(neck, pelvis)
    hip_x, hip_y, hip_z = orthonormal(up, sub(P["l_hip"], P["r_hip"]))
    out["pelvis"] = frame_matrix(hip_x, hip_y, hip_z, pelvis)
    ch_x, ch_y, ch_z = orthonormal(up, sub(P["l_shoulder"], P["r_shoulder"]))
    out["chest"] = frame_matrix(ch_x, ch_y, ch_z, neck)
    if "head" in P:
        hx, hy, hz = orthonormal(sub(P["head"], neck), ch_x)
        out["neck"] = frame_matrix(hx, hy, hz, neck)
        out["head"] = frame_matrix(hx, hy, hz, P["head"])
    else:
        out["neck"] = out["head"] = out["chest"]
    for side in "lr":
        for kind, (a, b, c), reference in (("arm", ("shoulder", "elbow", "wrist"), ch_x),
                                            ("leg", ("hip", "knee", "ankle"), hip_x)):
            ja, jb, jc = P[side + "_" + a], P[side + "_" + b], P[side + "_" + c]
            n = _limb_normal(ja, jb, jc, reference)
            if kind == "arm":
                out[side + "_upperarm"] = _segment(ja, sub(jb, ja), n)
                out[side + "_forearm"] = _segment(jb, sub(jc, jb), n)
                out[side + "_hand"] = out[side + "_hand_ik"] = _segment(jc, sub(jc, jb), n)
            else:
                out[side + "_thigh"] = _segment(ja, sub(jb, ja), n)
                out[side + "_shin"] = _segment(jb, sub(jc, jb), n)
                toe = P.get(side + "_toe")
                foot_aim = sub(toe, jc) if toe is not None else sub(jc, jb)
                out[side + "_foot"] = out[side + "_foot_ik"] = _segment(jc, foot_aim, n)
                if toe is not None:
                    out[side + "_toe"] = _segment(toe, foot_aim, n)
            out["%s_%s_pole" % (side, kind)] = pole_matrix(ja, jb, jc, n, pole_distance)
    return out


def pole_matrix(a: Vec, b: Vec, c: Vec, normal: Vec, distance: float = 1.0) -> Matrix:
    """Pole vector position: out from the middle joint, in the bend plane, one chain length away."""
    chain = length(sub(b, a)) + length(sub(c, b))
    axis = unit(sub(c, a))
    projected = add(a, scale(axis, dot(sub(b, a), axis)))
    direction = sub(b, projected)
    if length(direction) < 1e-3 * max(chain, 1e-9):
        direction = cross(axis, normal)  # straight limb: the side the joint bends to
    position = add(b, scale(unit(direction), chain * distance))
    return frame_matrix((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), position)


def offset(control_ref: Sequence[float], driver_ref: Sequence[float]) -> Matrix:
    return mat_mul(control_ref, mat_inverse(driver_ref))


def control_targets(offsets: Dict[str, Tuple[str, Matrix]], frames: Dict[int, Dict[str, Matrix]]
                    ) -> Dict[str, Dict[int, Matrix]]:
    """control -> frame -> world matrix, from ``offsets`` (control -> (driver, offset))."""
    out: Dict[str, Dict[int, Matrix]] = {}
    for control, (driver, off) in offsets.items():
        per_frame = {}
        for frame, drivers in frames.items():
            if driver in drivers:
                per_frame[frame] = mat_mul(off, drivers[driver])
        out[control] = per_frame
    return out


# ------------------------------------------------------------------------------------------ euler
def euler_xyz(m: Sequence[float]) -> Vec:
    """Rotation part of a Maya world matrix -> rotate X/Y/Z in degrees (rotate order xyz)."""
    rows = [unit((m[0], m[1], m[2]), (1, 0, 0)), unit((m[4], m[5], m[6]), (0, 1, 0)),
            unit((m[8], m[9], m[10]), (0, 0, 1))]
    r02 = max(-1.0, min(1.0, rows[0][2]))
    y = math.asin(-r02)
    if abs(r02) < 0.999999:
        x = math.atan2(rows[1][2], rows[2][2])
        z = math.atan2(rows[0][1], rows[0][0])
    else:  # gimbal: put everything into x
        x = math.atan2(-rows[2][1], rows[1][1])
        z = 0.0
    return (math.degrees(x), math.degrees(y), math.degrees(z))


def matrix_from_euler_xyz(rx: float, ry: float, rz: float, t: Sequence[float] = (0.0, 0.0, 0.0)) -> Matrix:
    """Inverse of :func:`euler_xyz` (Maya rotate order xyz, row vectors: R = Rx * Ry * Rz)."""
    x, y, z = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx_m = [1, 0, 0, 0, 0, cx, sx, 0, 0, -sx, cx, 0, 0, 0, 0, 1]
    ry_m = [cy, 0, -sy, 0, 0, 1, 0, 0, sy, 0, cy, 0, 0, 0, 0, 1]
    rz_m = [cz, sz, 0, 0, -sz, cz, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    m = mat_mul(mat_mul(rx_m, ry_m), rz_m)
    m[12], m[13], m[14] = float(t[0]), float(t[1]), float(t[2])
    return m


def unwrap(previous: Optional[Vec], current: Vec) -> Vec:
    """Shift each angle by multiples of 360 to stay closest to the previous key (no 180 flips in curves)."""
    if previous is None:
        return current
    return tuple(c + 360.0 * round((p - c) / 360.0) for p, c in zip(previous, current))  # type: ignore[return-value]


def channels_for(driver: str, override: Optional[str] = None) -> str:
    value = override if override in ("t", "r", "tr") else DRIVERS.get(driver, ("", "r"))[1]
    return value


def fit_positions(fit: Dict[str, Any], frame: int) -> Optional[Dict[str, Vec]]:
    """Body joint -> world position on ``frame`` from a ``roto_fit`` result document."""
    entry = (fit.get("frames") or {}).get(str(frame))
    if not entry:
        return None
    return {name: tuple(entry["p"][i]) for i, name in enumerate(fit["joints"])}  # type: ignore[misc]
