"""Which nodes of the artist's rig stand for the toolkit's body joints and drivers (Phase 04).

A *rig preset* (JSON, saved in the project) holds:

* ``joints`` - body joint -> rig **joint** (deformation skeleton). Their world positions at the
  reference frame give the bone lengths used by the fit, and they are read by the FK/IK tools.
* ``controls`` - driver -> rig **control** with the channels to bake. A driver is a body part
  frame computed from the fitted joints (``retarget.DRIVERS``).
* ``limbs`` - per limb FK/IK setup for the match tools (FK controls, IK control, pole vector,
  IK/FK switch attribute and its FK/IK values) plus the recorded match offsets.

Pure Python: no Qt, no Maya.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BODY_JOINTS: List[str] = [
    "pelvis", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle", "l_toe", "r_toe",
    "neck", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
]
#: same body as ``ai_engine/pose/skeleton.py`` (kept in sync by tests/unit/test_rotomation.py)
PARENT: Dict[str, Optional[str]] = {
    "pelvis": None, "l_hip": "pelvis", "r_hip": "pelvis", "neck": "pelvis",
    "l_knee": "l_hip", "r_knee": "r_hip", "l_ankle": "l_knee", "r_ankle": "r_knee",
    "l_toe": "l_ankle", "r_toe": "r_ankle", "head": "neck",
    "l_shoulder": "neck", "r_shoulder": "neck", "l_elbow": "l_shoulder", "r_elbow": "r_shoulder",
    "l_wrist": "l_elbow", "r_wrist": "r_elbow",
}
DRAW_BONES: List[Tuple[int, int]] = [(BODY_JOINTS.index(p), i) for i, p in
                                     ((i, PARENT[j]) for i, j in enumerate(BODY_JOINTS)) if p] + [
    (BODY_JOINTS.index("l_shoulder"), BODY_JOINTS.index("r_shoulder")),
    (BODY_JOINTS.index("l_hip"), BODY_JOINTS.index("r_hip"))]
REQUIRED_JOINTS: List[str] = [j for j in BODY_JOINTS if j not in ("head", "l_toe", "r_toe")]
JOINT_LABELS: Dict[str, str] = {
    "pelvis": "Hips / pelvis", "l_hip": "L upper leg (hip)", "r_hip": "R upper leg (hip)",
    "l_knee": "L knee", "r_knee": "R knee", "l_ankle": "L ankle", "r_ankle": "R ankle",
    "l_toe": "L toe / ball", "r_toe": "R toe / ball", "neck": "Neck", "head": "Head",
    "l_shoulder": "L upper arm (shoulder)", "r_shoulder": "R upper arm (shoulder)", "l_elbow": "L elbow",
    "r_elbow": "R elbow", "l_wrist": "L wrist", "r_wrist": "R wrist",
}
LIMBS: Dict[str, Tuple[str, str, str]] = {
    "l_arm": ("l_shoulder", "l_elbow", "l_wrist"), "r_arm": ("r_shoulder", "r_elbow", "r_wrist"),
    "l_leg": ("l_hip", "l_knee", "l_ankle"), "r_leg": ("r_hip", "r_knee", "r_ankle"),
}
LIMB_LABELS = {"l_arm": "Left arm", "r_arm": "Right arm", "l_leg": "Left leg", "r_leg": "Right leg"}

# Name patterns for auto-detection. A name is split into tokens (separators and camelCase); side
# tokens (L/Left/lf, R/Right/rt), centre tokens (M/C/Mid) and joint suffixes (jnt, bind...) are
# removed and the rest is joined: "mixamorig:LeftUpLeg" -> side l, base "upleg"; "Knee_L" -> l, "knee".
_LEFT = {"l", "left", "lf", "lft"}
_RIGHT = {"r", "right", "rt", "rgt"}
_DROP = {"m", "c", "ct", "ctr", "mid", "center", "centre", "jnt", "jn", "joint", "bind", "bnd", "jj", "skin",
         "sk", "drv", "def", "result", "rslt"}
_PATTERNS: Dict[str, List[str]] = {
    "pelvis": [r"^(?:hips?|pelvis)\d*$", r"^(?:root|cog)\d*$"],
    "hip": [r"^upleg$", r"^(?:thigh|femur|upperleg|legupper|hip)\d*$", r"^leg\d*$"],
    "knee": [r"^knee\d*$", r"^leg\d*$", r"^(?:shin|calf|lowerleg|leglower|tibia)\d*$"],
    "ankle": [r"^ankle\d*$", r"^foot\d*$"],
    "toe": [r"^(?:ball|toebase|toes?)\d*$"],
    "neck": [r"^neck\d*$"],
    "head": [r"^head\d*$"],
    "shoulder": [r"^arm\d*$", r"^(?:upperarm|armupper|uparm|humerus)\d*$", r"^shoulder\d*$"],
    "elbow": [r"^(?:forearm|elbow|lowerarm|armlower)\d*$"],
    "wrist": [r"^(?:wrist|hand)\d*$"],
}
_LIMB_PART = {"hip", "knee", "ankle", "toe", "shoulder", "elbow", "wrist"}


def short_name(node: str) -> str:
    return node.split("|")[-1].split(":")[-1]


def name_tokens(node: str) -> List[str]:
    parts = re.split(r"[^A-Za-z0-9]+", short_name(node))
    tokens: List[str] = []
    for part in parts:
        tokens.extend(t.lower() for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", part) if t)
    return tokens


def classify(node: str) -> Tuple[Optional[str], str]:
    """(side 'l' / 'r' / None, base name) of a rig node."""
    side: Optional[str] = None
    rest: List[str] = []
    for token in name_tokens(node):
        if token in _LEFT:
            side = "l"
        elif token in _RIGHT:
            side = "r"
        elif token not in _DROP:
            rest.append(token)
    return side, "".join(rest)


def auto_map(joints: Iterable[str]) -> Dict[str, str]:
    """Best-effort body joint -> rig joint guess from joint names (the artist checks and fixes it).

    ``joints`` should be in hierarchy order (parents first) so the match nearest the root wins.
    Patterns are tried in order across all joints, so e.g. Mixamo's ``LeftArm`` (upper arm) wins
    over ``LeftShoulder`` (the clavicle).
    """
    names = [(node,) + classify(node) for node in joints]
    result: Dict[str, str] = {}
    used = set()
    for part in ["pelvis", "neck", "head", "shoulder", "elbow", "wrist", "hip", "knee", "ankle", "toe"]:
        for pattern in _PATTERNS[part]:
            for node, side, base in names:
                if node in used or not re.search(pattern, base):
                    continue
                if part in _LIMB_PART:
                    if side is None:
                        continue
                    key = "%s_%s" % (side, part)
                elif side is not None:
                    continue
                else:
                    key = part
                if key in result:
                    continue
                result[key] = node
                used.add(node)
    return result


# ------------------------------------------------------------------------------------ preset
def empty_preset(name: str = "rig") -> Dict[str, Any]:
    return {"kind": "rotomation_rig", "version": 1, "name": name, "joints": {}, "controls": {},
            "limbs": {limb: {} for limb in LIMBS}, "reference_frame": None}


def missing_joints(preset: Dict[str, Any]) -> List[str]:
    joints = preset.get("joints") or {}
    return [j for j in REQUIRED_JOINTS if not joints.get(j)]


def validate_preset(preset: Any) -> Dict[str, Any]:
    if not isinstance(preset, dict) or preset.get("kind") != "rotomation_rig":
        raise ValueError("not a rotomation rig preset")
    out = empty_preset(str(preset.get("name") or "rig"))
    for key in ("joints", "controls", "limbs"):
        value = preset.get(key) or {}
        if not isinstance(value, dict):
            raise ValueError("%s must be an object" % key)
        out[key].update(value)
    out["joints"] = {k: str(v) for k, v in out["joints"].items() if k in BODY_JOINTS and v}
    out["reference_frame"] = preset.get("reference_frame")
    return out


def save_preset(preset: Dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validate_preset(preset), indent=2), encoding="utf-8")
    return path


def load_preset(path: Path) -> Dict[str, Any]:
    try:
        return validate_preset(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read rig preset %s: %s" % (path, exc))
