"""The toolkit's body skeleton and how each pose model's keypoints map onto it.

Every model layout (Halpe26, COCO17, MediaPipe33) is converted to the same 17 *body joints*.
The artist maps these joints to their own rig in Maya, and the fit works on them only.

Conventions: "l_" is the subject's own left. 2D keypoints are plate pixels (x right, y down) with a
confidence in [0, 1]. 3D model joints are converted to Maya camera space (x right, y up, the camera
looks down -Z), hip centred, in the model's own units (only directions are used).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

JOINTS: List[str] = [
    "pelvis", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle", "l_toe", "r_toe",
    "neck", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
]
INDEX: Dict[str, int] = {name: i for i, name in enumerate(JOINTS)}
#: Joints the fit cannot work without (head and toes are optional).
REQUIRED: List[str] = [j for j in JOINTS if j not in ("head", "l_toe", "r_toe")]

#: Parent of each joint in the fitted body (pelvis is the root).
PARENT: Dict[str, Optional[str]] = {
    "pelvis": None, "l_hip": "pelvis", "r_hip": "pelvis", "neck": "pelvis",
    "l_knee": "l_hip", "r_knee": "r_hip", "l_ankle": "l_knee", "r_ankle": "r_knee",
    "l_toe": "l_ankle", "r_toe": "r_ankle", "head": "neck",
    "l_shoulder": "neck", "r_shoulder": "neck", "l_elbow": "l_shoulder", "r_elbow": "r_shoulder",
    "l_wrist": "l_elbow", "r_wrist": "r_elbow",
}
#: Bones whose direction is free in the fit (rigid pelvis / chest blocks are handled separately).
LIMB_BONES: List[Tuple[str, str]] = [
    ("neck", "head"),
    ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"), ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist"),
    ("l_hip", "l_knee"), ("l_knee", "l_ankle"), ("l_ankle", "l_toe"),
    ("r_hip", "r_knee"), ("r_knee", "r_ankle"), ("r_ankle", "r_toe"),
]
PELVIS_BLOCK = ("pelvis", "l_hip", "r_hip")
CHEST_BLOCK = ("neck", "l_shoulder", "r_shoulder")
FEET = {"l": ("l_ankle", "l_toe"), "r": ("r_ankle", "r_toe")}
#: Lines drawn by the Maya viewer overlay.
DRAW_BONES: List[Tuple[str, str]] = [(PARENT[j], j) for j in JOINTS if PARENT[j]] + [("l_shoulder", "r_shoulder"),
                                                                                    ("l_hip", "r_hip")]

# ------------------------------------------------------------------------------------ layouts
# Each entry: body joint -> list of source keypoint indices (averaged; confidence = minimum).
HALPE26: Dict[str, List[int]] = {
    "pelvis": [11, 12], "l_hip": [11], "r_hip": [12], "l_knee": [13], "r_knee": [14],
    "l_ankle": [15], "r_ankle": [16], "l_toe": [20, 22], "r_toe": [21, 23], "neck": [18],
    "head": [3, 4], "l_shoulder": [5], "r_shoulder": [6], "l_elbow": [7], "r_elbow": [8],
    "l_wrist": [9], "r_wrist": [10],
}
COCO17: Dict[str, List[int]] = {
    "pelvis": [11, 12], "l_hip": [11], "r_hip": [12], "l_knee": [13], "r_knee": [14],
    "l_ankle": [15], "r_ankle": [16], "neck": [5, 6], "head": [3, 4], "l_shoulder": [5], "r_shoulder": [6],
    "l_elbow": [7], "r_elbow": [8], "l_wrist": [9], "r_wrist": [10],
}
MEDIAPIPE33: Dict[str, List[int]] = {
    "pelvis": [23, 24], "l_hip": [23], "r_hip": [24], "l_knee": [25], "r_knee": [26],
    "l_ankle": [27], "r_ankle": [28], "l_toe": [31], "r_toe": [32], "neck": [11, 12],
    "head": [7, 8], "l_shoulder": [11], "r_shoulder": [12], "l_elbow": [13], "r_elbow": [14],
    "l_wrist": [15], "r_wrist": [16],
}
LAYOUTS: Dict[str, Dict[str, List[int]]] = {"halpe26": HALPE26, "coco17": COCO17, "mediapipe33": MEDIAPIPE33}
KEYPOINT_COUNT = {"halpe26": 26, "coco17": 17, "mediapipe33": 33}


def to_body(points: Sequence[Sequence[float]], scores: Sequence[float], layout: str) -> List[List[float]]:
    """Model keypoints (``[[x, y], ...]`` or ``[[x, y, z], ...]``) -> body joints ``[[..., conf], ...]``.

    Joints the layout does not have get confidence 0 (and zeros for the coordinates).
    """
    table = LAYOUTS[layout]
    dims = len(points[0]) if len(points) else 2
    out: List[List[float]] = []
    for name in JOINTS:
        sources = table.get(name)
        if not sources or any(i >= len(points) for i in sources):
            out.append([0.0] * dims + [0.0])
            continue
        coords = [sum(float(points[i][d]) for i in sources) / len(sources) for d in range(dims)]
        conf = min(float(scores[i]) for i in sources)
        out.append(coords + [max(0.0, min(1.0, conf))])
    return out


def bbox_from_body(body: Sequence[Sequence[float]], min_conf: float = 0.3,
                   pad: float = 0.15) -> Optional[List[float]]:
    """Box around the confident 2D joints, padded by ``pad`` of its size (used to follow the subject)."""
    pts = [(p[0], p[1]) for p in body if p[-1] >= min_conf]
    if len(pts) < 4:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    size = max(w, h, 1.0)
    return [min(xs) - pad * size, min(ys) - pad * size, max(xs) + pad * size, max(ys) + pad * size]
