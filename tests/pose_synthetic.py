"""Synthetic rotomation data: a rig-sized body walking past a moving Maya camera (tests only)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

REST: Dict[str, Tuple[float, float, float]] = {   # cm, Y up, character faces +Z
    "pelvis": (0.0, 100.0, 0.0), "l_hip": (9.0, 95.0, 0.0), "r_hip": (-9.0, 95.0, 0.0),
    "l_knee": (9.5, 52.0, 1.0), "r_knee": (-9.5, 52.0, 1.0), "l_ankle": (10.0, 9.0, -2.0),
    "r_ankle": (-10.0, 9.0, -2.0), "l_toe": (10.5, 2.0, 12.0), "r_toe": (-10.5, 2.0, 12.0),
    "neck": (0.0, 150.0, -1.0), "head": (0.0, 165.0, 1.0), "l_shoulder": (17.0, 146.0, -2.0),
    "r_shoulder": (-17.0, 146.0, -2.0), "l_elbow": (20.0, 118.0, -4.0), "r_elbow": (-20.0, 118.0, -4.0),
    "l_wrist": (22.0, 93.0, 2.0), "r_wrist": (-22.0, 93.0, 2.0),
}


def _rot_x(a: float) -> Any:
    import numpy as np
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(a: float) -> Any:
    import numpy as np
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def walk(frames: List[int], turn: float = 0.0) -> Dict[int, Dict[str, Any]]:
    """World joint positions per frame: walking along +X (facing +X), legs and arms swinging."""
    import numpy as np
    rest = {k: np.array(v, float) for k, v in REST.items()}
    out = {}
    for f in frames:
        t = (f - frames[0]) / 24.0
        phase = 2 * math.pi * 1.0 * t
        heading = _rot_y(math.pi / 2 + turn * t)  # face +X
        pos = {}
        pelvis = np.array([60.0 * t * 1.0, 100.0 + 2.0 * math.cos(2 * phase), 0.0])

        def chain(names, swing, knee):
            hip = rest[names[0]] - rest["pelvis"]
            up = _rot_x(-swing)
            p_hip = hip
            p_knee = p_hip + up @ (rest[names[1]] - rest[names[0]])
            low = up @ _rot_x(knee)
            p_ank = p_knee + low @ (rest[names[2]] - rest[names[1]])
            res = [p_hip, p_knee, p_ank]
            if len(names) > 3:
                res.append(p_ank + low @ (rest[names[3]] - rest[names[2]]))
            return res

        legs = {"l": chain(["l_hip", "l_knee", "l_ankle", "l_toe"], 0.45 * math.sin(phase), 0.5 * max(0, math.sin(phase + 1.2))),
                "r": chain(["r_hip", "r_knee", "r_ankle", "r_toe"], 0.45 * math.sin(phase + math.pi), 0.5 * max(0, math.sin(phase + math.pi + 1.2)))}
        arms = {"l": chain(["l_shoulder", "l_elbow", "l_wrist"], -0.35 * math.sin(phase), -0.4),
                "r": chain(["r_shoulder", "r_elbow", "r_wrist"], -0.35 * math.sin(phase + math.pi), -0.4)}
        local = {"pelvis": np.zeros(3), "neck": rest["neck"] - rest["pelvis"], "head": rest["head"] - rest["pelvis"]}
        for side in "lr":
            for name, p in zip(["hip", "knee", "ankle", "toe"], legs[side]):
                local[side + "_" + name] = p
            for name, p in zip(["shoulder", "elbow", "wrist"], arms[side]):
                local[side + "_" + name] = p
        for k, v in local.items():
            pos[k] = pelvis + heading @ v
        out[f] = pos
    return out


def look_at(eye: Any, target: Any) -> List[float]:
    """Maya-style camera world matrix (row-major, row vectors) looking from eye to target (Y up)."""
    import numpy as np
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = eye - target
    z /= np.linalg.norm(z)
    x = np.cross([0.0, 1.0, 0.0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3], m[3, :3] = x, y, z, eye
    return [float(v) for v in m.ravel()]


def camera_sample(frame: int, frames: List[int]) -> Dict[str, Any]:
    t = (frame - frames[0]) / 24.0
    eye = [60.0 * t - 50.0, 120.0, 520.0]
    target = [60.0 * t, 100.0, 0.0]
    return {"world_matrix": look_at(eye, target), "focal_mm": 35.0, "h_aperture_in": 1.417,
            "v_aperture_in": 0.945, "film_fit": 1, "h_offset_in": 0.0, "v_offset_in": 0.0, "lens_squeeze": 1.0}


def observations(truth: Dict[int, Dict[str, Any]], cameras: Dict[int, Dict[str, Any]], width: int, height: int,
                 noise_px: float = 0.0, seed: int = 1) -> Dict[int, Dict[str, Any]]:
    """2D body joints (+ noisy camera-space 3D prior) the way the pose job writes them."""
    import numpy as np
    from ai_engine.pose import skeleton as sk
    from ai_engine.pose.camera import camera_from_dict
    rng = np.random.default_rng(seed)
    out = {}
    for f, joints in truth.items():
        cam = camera_from_dict(cameras[f], width, height)
        pts = np.stack([joints[j] for j in sk.JOINTS])
        uv, _ = cam.project(pts)
        uv = uv + rng.normal(0.0, noise_px, uv.shape)
        cam_space = pts @ cam.inverse[:3, :3] + cam.inverse[3, :3]
        cam_space = cam_space - cam_space[sk.INDEX["pelvis"]]
        cam_space = cam_space / 100.0 + rng.normal(0.0, 0.02, cam_space.shape)  # metres, ~2 cm noise
        out[f] = {"kp2d": [[float(u), float(v), 0.9] for u, v in uv],
                  "kp3d": [[float(x), float(y), float(z), 0.9] for x, y, z in cam_space]}
    return out


def stand(frames: List[int]) -> Dict[int, Dict[str, Any]]:
    """Standing still with both feet planted, arms waving (for the foot-lock test)."""
    import numpy as np
    rest = {k: np.array(v, float) for k, v in REST.items()}
    heading = _rot_y(math.pi / 4)
    out = {}
    for f in frames:
        wave = 0.6 * math.sin((f - frames[0]) / 4.0)
        pos = {}
        for name, p in rest.items():
            local = p - rest["pelvis"]
            if name in ("l_elbow", "l_wrist", "r_elbow", "r_wrist"):
                side = name[0]
                shoulder = rest[side + "_shoulder"] - rest["pelvis"]
                local = shoulder + _rot_x(wave if side == "l" else -wave) @ (local - shoulder)
            pos[name] = rest["pelvis"] + heading @ local
        out[f] = pos
    return out


def static_camera(frame: int, frames: List[int]) -> Dict[str, Any]:
    return {"world_matrix": look_at([150.0, 130.0, 420.0], [0.0, 90.0, 0.0]), "focal_mm": 35.0,
            "h_aperture_in": 1.417, "v_aperture_in": 0.945, "film_fit": 1, "h_offset_in": 0.0,
            "v_offset_in": 0.0, "lens_squeeze": 1.0}


def fit_frames(truth, cameras, width, height, noise_px=1.5, prior=True, seed=1):
    """FrameData list for the fitter from synthetic truth."""
    from ai_engine.pose import fit
    from ai_engine.pose.camera import camera_from_dict
    model = fit.BodyModel(REST)
    obs = observations(truth, cameras, width, height, noise_px, seed)
    return model, [fit.FrameData(f, camera_from_dict(cameras[f], width, height),
                                 fit.select_joints(obs[f]["kp2d"], model.joints),
                                 fit.select_joints(obs[f]["kp3d"], model.joints) if prior else None)
                   for f in sorted(truth)]
