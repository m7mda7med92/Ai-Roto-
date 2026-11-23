"""Fit the artist's rig proportions to the 2D pose through the tracked Maya camera (Phase 04).

Body model (per frame, in Maya world space)
-------------------------------------------
* pelvis position ``P`` (3) - the root.
* a rigid **pelvis block** (pelvis, both hips) with rotation ``rp`` (3, rotation vector), and
* a rigid **chest block** (neck, both shoulders) hung from the pelvis with rotation ``rc`` (3),
* every other bone (head, arms, legs, feet) is a free direction (3 each, normalised) with the
  **length measured on the artist's rig** in Maya.

Bone lengths never change, so the depth of the body follows from the camera and the rig size.

Cost (``scipy.optimize.least_squares``, robust soft-L1 loss)
-------------------------------------------------------------
* reprojection: projected joints vs. 2D keypoints, weighted by keypoint confidence;
* 3D prior: bone directions vs. the 3D body model (resolves the front/back depth ambiguity);
* smoothness: joint acceleration between consecutive frames (global pass only);
* foot contact: a planted foot does not slide, and soles stay above the ground height.

Solve order: frame by frame (warm-started from the previous frame), then one global pass over the
whole range with smoothness and foot contacts. Everything is plain numpy/scipy, CPU.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ai_engine.pose import skeleton as sk
from ai_engine.pose.camera import Camera

log = logging.getLogger("ai_engine.pose.fit")

JOINT_WEIGHT = {"pelvis": 0.5, "head": 0.5, "neck": 0.7, "l_toe": 0.5, "r_toe": 0.5}


@dataclass
class FitOptions:
    min_confidence: float = 0.25
    prior_weight: float = 2.0
    smooth_weight: float = 8.0
    foot_lock: bool = True
    contact_weight: float = 25.0
    ground_height: Optional[float] = None     # world Y of the ground; None = estimate from the feet
    use_ground: bool = True
    contact_height: float = 0.12              # x leg length: max sole height for a contact
    contact_speed: float = 0.025              # x leg length per frame: max sole speed for a contact
    pixel_sigma: Optional[float] = None       # px; default height / 360 (3 px at 1080p)
    max_iterations: int = 60
    global_pass: bool = True
    up_axis: str = "y"                        # the scene's up axis ("y" or "z")

    #: name -> (type, min, max) for client-supplied options
    LIMITS = {
        "min_confidence": (float, 0.0, 1.0), "prior_weight": (float, 0.0, 100.0),
        "smooth_weight": (float, 0.0, 1000.0), "foot_lock": (bool, None, None),
        "contact_weight": (float, 0.0, 1000.0), "ground_height": (float, -1e7, 1e7),
        "use_ground": (bool, None, None), "contact_height": (float, 0.0, 1.0),
        "contact_speed": (float, 0.0, 1.0), "pixel_sigma": (float, 0.1, 100.0),
        "max_iterations": (int, 1, 1000), "global_pass": (bool, None, None), "up_axis": (str, None, None),
    }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FitOptions":
        """Validated options; raises ``ValueError`` naming the bad key."""
        options = cls()
        if data is None:
            return options
        if not isinstance(data, dict):
            raise ValueError("options must be an object")
        for key, value in data.items():
            if key not in cls.LIMITS:
                raise ValueError("unknown fit option '%s'" % key)
            kind, lo, hi = cls.LIMITS[key]
            if value is None and key in ("ground_height", "pixel_sigma"):
                setattr(options, key, None)
                continue
            if kind is bool:
                if not isinstance(value, bool):
                    raise ValueError("%s must be true or false" % key)
            elif kind is str:
                if value not in ("y", "z"):
                    raise ValueError("up_axis must be 'y' or 'z'")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                    raise ValueError("%s must be a number in [%s, %s]" % (key, lo, hi))
                value = kind(value)
            setattr(options, key, value)
        return options

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ------------------------------------------------------------------------------------------ math
def rodrigues(r: Any) -> Any:
    """Rotation vectors (..., 3) -> matrices (..., 3, 3) (column-vector convention)."""
    import numpy as np  # type: ignore
    theta = np.linalg.norm(r, axis=-1, keepdims=True)
    small = theta < 1e-9
    k = np.where(small, 0.0, r / np.where(small, 1.0, theta))
    kx, ky, kz = k[..., 0], k[..., 1], k[..., 2]
    zero = np.zeros_like(kx)
    kmat = np.stack([np.stack([zero, -kz, ky], -1), np.stack([kz, zero, -kx], -1),
                     np.stack([-ky, kx, zero], -1)], -2)
    t = theta[..., None]
    eye = np.broadcast_to(np.eye(3), kmat.shape)
    return eye + np.sin(t) * kmat + (1 - np.cos(t)) * (kmat @ kmat)


def rotvec(matrix: Any) -> Any:
    """3x3 rotation -> rotation vector (inverse of :func:`rodrigues`)."""
    import numpy as np  # type: ignore
    from scipy.spatial.transform import Rotation  # type: ignore
    return Rotation.from_matrix(np.asarray(matrix, dtype=float)).as_rotvec()


def kabsch(a: Any, b: Any) -> Any:
    """Rotation R (3x3) minimising sum |R a_i - b_i|^2 (rows of a, b are vectors)."""
    import numpy as np  # type: ignore
    h = np.asarray(a).T @ np.asarray(b)
    u, _s, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def body_frame(left: Any, right: Any, up_from: Any, up_to: Any) -> Any:
    """Orthonormal frame (columns x=left, y=up, z=forward) from a left-right line and an up line."""
    import numpy as np  # type: ignore
    x = np.asarray(left, float) - np.asarray(right, float)
    x /= max(np.linalg.norm(x), 1e-9)
    y = np.asarray(up_to, float) - np.asarray(up_from, float)
    y = y - x * float(np.dot(x, y))
    y /= max(np.linalg.norm(y), 1e-9)
    z = np.cross(x, y)
    return np.stack([x, y, z], axis=1)


# ------------------------------------------------------------------------------------------ model
class BodyModel:
    """Parameterisation of the body with the rig's proportions (rest = rig joints at the reference frame)."""

    def __init__(self, rest: Dict[str, Sequence[float]]) -> None:
        import numpy as np  # type: ignore
        missing = [j for j in sk.REQUIRED if j not in rest]
        if missing:
            raise ValueError("the rig mapping is missing: %s" % ", ".join(missing))
        self.joints = [j for j in sk.JOINTS if j in rest]
        self.index = {j: i for i, j in enumerate(self.joints)}
        x = {j: np.array([float(v) for v in rest[j]], dtype=float) for j in self.joints}
        self.rest = np.stack([x[j] for j in self.joints])
        p0 = x["pelvis"]
        self.pelvis_frame = body_frame(x["l_hip"], x["r_hip"], p0, x["neck"])
        self.chest_frame = body_frame(x["l_shoulder"], x["r_shoulder"], p0, x["neck"])
        self.pelvis_offsets = {j: x[j] - p0 for j in sk.PELVIS_BLOCK}
        self.chest_offsets = {j: x[j] - p0 for j in sk.CHEST_BLOCK}
        self.limbs = [(a, b) for a, b in sk.LIMB_BONES if a in x and b in x]
        self.lengths = np.array([np.linalg.norm(x[b] - x[a]) for a, b in self.limbs])
        if np.any(self.lengths < 1e-6):
            bad = [b for (a, b), length in zip(self.limbs, self.lengths) if length < 1e-6]
            raise ValueError("rig joints sit on top of each other (zero-length bones): %s" % ", ".join(bad))
        if np.linalg.norm(x["l_hip"] - x["r_hip"]) < 1e-6 or np.linalg.norm(x["l_shoulder"] - x["r_shoulder"]) < 1e-6:
            raise ValueError("left and right hips / shoulders must be different joints")
        self.size = float(self.lengths.mean())
        self.leg_length = float(np.mean([np.linalg.norm(x[s + "_hip"] - x[s + "_knee"])
                                         + np.linalg.norm(x[s + "_knee"] - x[s + "_ankle"]) for s in "lr"]))
        self.torso_length = float(np.linalg.norm(x["neck"] - p0))
        self.n_vars = 9 + 3 * len(self.limbs)
        self.bones = [(sk.PARENT[j], j) for j in self.joints if sk.PARENT[j] in self.index]

    # -------------------------------------------------------------- positions
    def positions(self, state: Any) -> Any:
        """state (N, n_vars) -> joint positions (N, J, 3)."""
        import numpy as np  # type: ignore
        n = state.shape[0]
        out = np.zeros((n, len(self.joints), 3))
        pelvis = state[:, 0:3]
        rp = rodrigues(state[:, 3:6])
        rc = rodrigues(state[:, 6:9])
        for j, offset in self.pelvis_offsets.items():
            out[:, self.index[j]] = pelvis + rp @ offset
        for j, offset in self.chest_offsets.items():
            out[:, self.index[j]] = pelvis + rc @ offset
        for k, (a, b) in enumerate(self.limbs):
            u = state[:, 9 + 3 * k:12 + 3 * k]
            norm = np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            out[:, self.index[b]] = out[:, self.index[a]] + u / norm * self.lengths[k]
        return out

    def limb_norms(self, state: Any) -> Any:
        import numpy as np  # type: ignore
        u = state[:, 9:].reshape(state.shape[0], len(self.limbs), 3)
        return (np.linalg.norm(u, axis=2) - 1.0) * 10.0

    # -------------------------------------------------------------- initial state
    def initial_state(self, positions: Dict[str, Any]) -> Any:
        """A state close to target world ``positions`` (missing joints follow the rest pose)."""
        import numpy as np  # type: ignore
        state = np.zeros(self.n_vars)
        pelvis = np.asarray(positions.get("pelvis", self.rest[self.index["pelvis"]]), float)
        state[0:3] = pelvis
        blocks = ((3, {"l_hip": self.pelvis_offsets["l_hip"], "r_hip": self.pelvis_offsets["r_hip"],
                       "neck": self.chest_offsets["neck"]}),
                  (6, {j: self.chest_offsets[j] for j in sk.CHEST_BLOCK}))
        for slot, offsets in blocks:
            names = [j for j in offsets if j in positions]
            if len(names) >= 2:
                a = np.stack([offsets[j] for j in names])
                b = np.stack([np.asarray(positions[j], float) - pelvis for j in names])
                state[slot:slot + 3] = rotvec(kabsch(a, b))
        if not any(j in positions for j in ("l_shoulder", "r_shoulder")):
            state[6:9] = state[3:6]  # no chest information: turn the chest with the pelvis
        delta_chest = rodrigues(state[6:9])
        delta_pelvis = rodrigues(state[3:6])
        for k, (a, b) in enumerate(self.limbs):
            if a in positions and b in positions:
                direction = np.asarray(positions[b], float) - np.asarray(positions[a], float)
            else:
                rest_dir = self.rest[self.index[b]] - self.rest[self.index[a]]
                upper = a in self.chest_offsets or a in ("l_elbow", "r_elbow", "head")
                direction = (delta_chest if upper else delta_pelvis) @ rest_dir
            norm = np.linalg.norm(direction)
            state[9 + 3 * k:12 + 3 * k] = direction / norm if norm > 1e-9 else [0.0, -1.0, 0.0]
        return state


# ------------------------------------------------------------------------------------------ data
@dataclass
class FrameData:
    frame: int
    camera: Camera
    obs: Any                       # (J, 3) x, y, confidence for model joints
    prior: Optional[Any] = None    # (J, 4) camera-space xyz + confidence, or None


@dataclass
class FitResult:
    frames: List[int]
    joints: List[str]
    positions: Any                 # (N, J, 3)
    reprojection_px: Any           # (N, J) nan where not observed
    contacts: Dict[str, List[bool]] = field(default_factory=dict)
    ground_height: Optional[float] = None


def select_joints(body: Sequence[Sequence[float]], joints: Sequence[str]) -> Any:
    import numpy as np  # type: ignore
    return np.array([body[sk.INDEX[j]] for j in joints], dtype=float)


# ------------------------------------------------------------------------------------------ solver
class RotoFitter:
    def __init__(self, model: BodyModel, frames: List[FrameData], options: FitOptions,
                 checkpoint: Callable[[], None] = lambda: None,
                 report: Callable[[float, str], None] = lambda p, m: None) -> None:
        import numpy as np  # type: ignore
        self.model = model
        self.data = sorted(frames, key=lambda d: d.frame)
        self.options = options
        self.checkpoint = checkpoint
        self.report = report
        height = self.data[0].camera.height if self.data else 1080
        self.sigma = float(options.pixel_sigma or max(1.0, height / 360.0))
        self.weights = np.array([JOINT_WEIGHT.get(j, 1.0) for j in model.joints])
        self.bone_a = np.array([model.index[a] for a, _b in model.bones], dtype=int)
        self.bone_b = np.array([model.index[b] for _a, b in model.bones], dtype=int)
        # Per-frame arrays so residuals are computed for many frames at once.
        n = len(self.data)
        self.inv = np.stack([d.camera.inverse for d in self.data]) if n else np.zeros((0, 4, 4))
        self.cam = np.array([[d.camera.focal_mm, d.camera.view_w_mm, d.camera.view_h_mm, d.camera.h_offset_mm,
                              d.camera.v_offset_mm, d.camera.width, d.camera.height] for d in self.data]).reshape(n, 7)
        obs = np.stack([d.obs for d in self.data]) if n else np.zeros((0, len(model.joints), 3))
        self.obs_uv = obs[..., :2]
        conf = np.where(obs[..., 2] >= options.min_confidence, obs[..., 2], 0.0)
        self.obs_w = np.sqrt(conf) * self.weights / self.sigma
        self.prior_dirs = np.zeros((n, len(self.bone_a), 3))
        self.prior_w = np.zeros((n, len(self.bone_a)))
        for i, d in enumerate(self.data):
            if d.prior is None:
                continue
            world = d.prior[:, :3] @ d.camera.rotation  # camera space -> world (row vectors)
            v = world[self.bone_b] - world[self.bone_a]
            norm = np.linalg.norm(v, axis=1)
            c = np.minimum(d.prior[self.bone_a, 3], d.prior[self.bone_b, 3])
            good = (norm > 1e-9) & (c >= 0.2)
            self.prior_dirs[i, good] = v[good] / norm[good, None]
            self.prior_w[i] = np.where(good, np.sqrt(c) * options.prior_weight, 0.0)
        self.residuals_per_frame = len(model.joints) * 3 + len(self.bone_a) * 3

    # -------------------------------------------------------------- helpers
    def project(self, positions: Any, idx: Any) -> Tuple[Any, Any]:
        """positions (k, J, 3) for frames ``idx`` -> pixels (k, J, 2), depth (k, J)."""
        import numpy as np  # type: ignore
        inv = self.inv[idx]
        cam = np.einsum("kjc,kcd->kjd", positions, inv[:, :3, :3]) + inv[:, None, 3, :3]
        depth = -cam[..., 2]
        safe = np.maximum(depth, 1e-6)
        c = self.cam[idx]
        focal, view_w, view_h, hoff, voff, width, height = (c[:, i:i + 1] for i in range(7))
        x_mm = focal * cam[..., 0] / safe - hoff
        y_mm = focal * cam[..., 1] / safe - voff
        uv = np.stack([(x_mm / view_w + 0.5) * width, (0.5 - y_mm / view_h) * height], axis=-1)
        return uv, depth

    def _frame_residuals(self, positions: Any, indices: Any) -> List[Any]:
        """Reprojection + prior residuals for frames ``indices`` (positions (k, J, 3)); fixed size per frame."""
        import numpy as np  # type: ignore
        idx = np.asarray(list(indices), dtype=int)
        uv, depth = self.project(positions, idx)
        reproj = (uv - self.obs_uv[idx]) * self.obs_w[idx][..., None]
        front = np.minimum(depth - 1e-3, 0.0) * 1e3  # stay in front of the camera
        bones = positions[:, self.bone_b] - positions[:, self.bone_a]
        bones = bones / np.maximum(np.linalg.norm(bones, axis=2, keepdims=True), 1e-9)
        prior = (bones - self.prior_dirs[idx]) * self.prior_w[idx][..., None]
        return [np.concatenate([reproj.reshape(len(idx), -1), front, prior.reshape(len(idx), -1)], axis=1).ravel()]

    # -------------------------------------------------------------- initialisation
    def _initial_targets(self, i: int) -> Dict[str, Any]:
        """Rough 3D joint guesses for frame ``i`` from the 2D pose (+ 3D prior) and the rig size."""
        import numpy as np  # type: ignore
        d, m = self.data[i], self.model
        obs = d.obs
        ok = obs[:, 2] >= self.options.min_confidence
        ip, ineck = m.index["pelvis"], m.index["neck"]
        if ok[ip] and ok[ineck]:
            torso_px = float(np.linalg.norm(obs[ip, :2] - obs[ineck, :2]))
            depth = d.camera.focal_px * m.torso_length / max(torso_px, 1.0)
        else:
            ys = obs[ok, 1]
            height_px = float(ys.max() - ys.min()) if ys.size >= 2 else d.camera.height / 2.0
            depth = d.camera.focal_px * (m.torso_length + m.leg_length) * 1.1 / max(height_px, 1.0)
        center = obs[ip, :2] if ok[ip] else (obs[ok, :2].mean(axis=0) if ok.any() else
                                             np.array([d.camera.width / 2.0, d.camera.height / 2.0]))
        pelvis = d.camera.position + depth * d.camera.ray(*center)
        targets: Dict[str, Any] = {"pelvis": pelvis}
        prior = d.prior
        if prior is not None and np.sum(prior[:, 3] >= 0.3) >= 6:
            world = prior[:, :3] @ d.camera.rotation
            scale = m.torso_length / max(np.linalg.norm(world[ineck] - world[ip]), 1e-9)
            for j, k in m.index.items():
                if prior[k, 3] >= 0.3:
                    targets[j] = pelvis + (world[k] - world[ip]) * scale
        else:
            for j, k in m.index.items():
                if ok[k]:
                    targets[j] = d.camera.position + depth * d.camera.ray(*obs[k, :2])
            targets["pelvis"] = pelvis
        return targets

    # -------------------------------------------------------------- per-frame pass
    def _solve_frame(self, i: int, start: Any, previous: Optional[Any]) -> Any:
        import numpy as np  # type: ignore
        from scipy.optimize import least_squares  # type: ignore
        m = self.model

        def batch(states: Any) -> Any:
            """Residuals (k, R) for k candidate states of frame ``i`` (one vectorised evaluation)."""
            pos = m.positions(states)
            k = states.shape[0]
            parts = [self._frame_residuals(pos, [i] * k)[0].reshape(k, -1), m.limb_norms(states)]
            if previous is not None:
                parts.append(((pos - previous[None]) / m.size * 0.5).reshape(k, -1))
            return np.concatenate(parts, axis=1)

        def residuals(x: Any) -> Any:
            return batch(x[None])[0]

        def jacobian(x: Any) -> Any:
            # Forward differences for all variables in a single batched evaluation.
            step = 1e-6 * np.maximum(1.0, np.abs(x))
            candidates = np.vstack([x[None], x[None] + np.diag(step)])
            values = batch(candidates)
            return ((values[1:] - values[0]) / step[:, None]).T

        result = least_squares(residuals, start, jac=jacobian, method="trf", loss="soft_l1", f_scale=1.0,
                               ftol=1e-6, xtol=1e-6, x_scale="jac", max_nfev=self.options.max_iterations * 4)
        return result.x

    def _solve_sequential(self) -> Any:
        import numpy as np  # type: ignore
        m = self.model
        states = np.zeros((len(self.data), m.n_vars))
        prev_state: Optional[Any] = None
        prev_frame: Optional[int] = None
        for i, d in enumerate(self.data):
            self.checkpoint()
            contiguous = prev_state is not None and prev_frame is not None and d.frame - prev_frame <= 2
            if contiguous:
                start = prev_state.copy()
                previous = m.positions(prev_state[None])[0]
            else:
                start = m.initial_state(self._initial_targets(i))
                previous = None
            states[i] = self._solve_frame(i, start, previous)
            guided = self._with_prior_limbs(i, states[i])
            if guided is not None:
                # Monocular depth flips (a knee bending forward instead of back) are local minima the
                # warm start cannot leave; restart the limbs from the 3D model and keep the better fit.
                alternative = self._solve_frame(i, guided, previous)
                if self._cost(i, alternative, previous) < self._cost(i, states[i], previous):
                    states[i] = alternative
            if not contiguous:
                # A cold start can land in the mirrored (front/back) pose; try the flipped facing too.
                flipped = start.copy()
                flipped[3:9] = [*rotvec(rodrigues(np.array([0.0, math.pi, 0.0])) @ rodrigues(start[3:6])),
                                *rotvec(rodrigues(np.array([0.0, math.pi, 0.0])) @ rodrigues(start[6:9]))]
                alternative = self._solve_frame(i, flipped, None)
                if self._cost(i, alternative) < self._cost(i, states[i]):
                    states[i] = alternative
            prev_state, prev_frame = states[i], d.frame
            self.report(0.05 + 0.55 * (i + 1) / len(self.data), "Fitting frame %d (%d/%d)" % (
                d.frame, i + 1, len(self.data)))
        return states

    def _with_prior_limbs(self, i: int, state: Any) -> Optional[Any]:
        """``state`` with every limb pointing along the 3D model's bone, if that differs noticeably."""
        import numpy as np  # type: ignore
        if not self.prior_w[i].any():
            return None
        guided = state.copy()
        changed = False
        for k, (_a, b) in enumerate(self.model.limbs):
            bone = int(np.nonzero(self.bone_b == self.model.index[b])[0][0])
            if self.prior_w[i, bone] <= 0:
                continue
            current = state[9 + 3 * k:12 + 3 * k] / max(np.linalg.norm(state[9 + 3 * k:12 + 3 * k]), 1e-9)
            if float(np.dot(current, self.prior_dirs[i, bone])) < 0.95:
                guided[9 + 3 * k:12 + 3 * k] = self.prior_dirs[i, bone]
                changed = True
        return guided if changed else None

    def _cost(self, i: int, state: Any, previous: Optional[Any] = None) -> float:
        import numpy as np  # type: ignore
        pos = self.model.positions(state[None])
        r = np.concatenate(self._frame_residuals(pos, [i]))
        if previous is not None:
            r = np.concatenate([r, ((pos[0] - previous) / self.model.size * 0.5).ravel()])
        return float(np.sum(2.0 * (np.sqrt(1.0 + r ** 2) - 1.0)))  # soft-L1, as the solver sees it

    # -------------------------------------------------------------- contacts
    def _soles(self, positions: Any) -> Dict[str, Any]:
        m = self.model
        return {side: positions[:, m.index[toe] if toe in m.index else m.index[ankle]]
                for side, (ankle, toe) in sk.FEET.items()}

    def detect_contacts(self, positions: Any) -> Tuple[Dict[str, Any], float]:
        import numpy as np  # type: ignore
        soles = self._soles(positions)
        up = 2 if self.options.up_axis == "z" else 1
        flat = [0, 2] if up == 1 else [0, 1]
        heights = np.concatenate([s[:, up] for s in soles.values()])
        ground = self.options.ground_height
        if ground is None:
            ground = float(np.percentile(heights, 5)) if heights.size else 0.0
        frames = np.array([d.frame for d in self.data])
        contacts = {}
        for side, raw in soles.items():
            sole = _moving_average(raw, 5)  # the per-frame solve jitters; contacts are judged on the trend
            speed = np.zeros(len(sole))
            if len(sole) > 1:
                step = np.linalg.norm(np.diff(sole[:, flat], axis=0), axis=1) / np.maximum(np.diff(frames), 1)
                speed[1:] = step
                speed[0] = step[0]
                speed[1:-1] = np.minimum(step[:-1], step[1:])
            low = (sole[:, up] - ground) < self.options.contact_height * self.model.leg_length
            slow = speed < self.options.contact_speed * self.model.leg_length
            flags = low & slow
            contacts[side] = _min_runs(flags, 3)
        return contacts, ground

    # -------------------------------------------------------------- global pass
    def _solve_global(self, states: Any, contacts: Dict[str, Any], ground: Optional[float]) -> Any:
        import numpy as np  # type: ignore
        from scipy.optimize import least_squares  # type: ignore
        from scipy.sparse import lil_matrix  # type: ignore
        m = self.model
        n, v = states.shape
        frames = [d.frame for d in self.data]
        triples = [(i - 1, i, i + 1) for i in range(1, n - 1)
                   if frames[i] - frames[i - 1] == 1 and frames[i + 1] - frames[i] == 1]
        pairs = [(i - 1, i) for i in range(1, n) if frames[i] - frames[i - 1] == 1]
        lock = []
        if self.options.foot_lock:
            for side, (ankle, toe) in sk.FEET.items():
                for a, b in pairs:
                    if contacts[side][a] and contacts[side][b]:
                        lock.extend((a, b, m.index[j]) for j in (ankle, toe) if j in m.index)
        soles = [m.index[toe] if toe in m.index else m.index[ankle] for ankle, toe in sk.FEET.values()]
        use_ground = self.options.use_ground and ground is not None and self.options.foot_lock
        size = m.size
        up = 2 if self.options.up_axis == "z" else 1

        def residuals(flat: Any) -> Any:
            state = flat.reshape(n, v)
            pos = m.positions(state)
            parts = self._frame_residuals(pos, range(n))
            parts.append(m.limb_norms(state).ravel())
            if triples:
                t = np.array(triples)
                accel = pos[t[:, 0]] - 2 * pos[t[:, 1]] + pos[t[:, 2]]
                parts.append((accel / size * self.options.smooth_weight).ravel())
            if lock:
                k = np.array(lock)
                parts.append(((pos[k[:, 1], k[:, 2]] - pos[k[:, 0], k[:, 2]]) / size
                              * self.options.contact_weight).ravel())
            if use_ground:
                parts.append((np.minimum(pos[:, soles, up] - ground, 0.0) / size * self.options.contact_weight).ravel())
            return np.concatenate(parts)

        # Jacobian sparsity: which frame's variables each residual depends on.
        per_frame_counts = [self.residuals_per_frame] * n
        rows = sum(per_frame_counts) + n * len(m.limbs) + 3 * len(m.joints) * len(triples) + 3 * len(lock) \
            + (n * len(soles) if use_ground else 0)
        sparsity = lil_matrix((rows, n * v), dtype=int)
        r = 0
        for i, count in enumerate(per_frame_counts):
            sparsity[r:r + count, i * v:(i + 1) * v] = 1
            r += count
        for i in range(n):
            sparsity[r:r + len(m.limbs), i * v + 9:(i + 1) * v] = 1
            r += len(m.limbs)
        for a, b, c in triples:
            for f in (a, b, c):
                sparsity[r:r + 3 * len(m.joints), f * v:(f + 1) * v] = 1
            r += 3 * len(m.joints)
        for a, b, _j in lock:
            sparsity[r:r + 3, a * v:(a + 1) * v] = 1
            sparsity[r:r + 3, b * v:(b + 1) * v] = 1
            r += 3
        if use_ground:
            for i in range(n):
                sparsity[r:r + len(soles), i * v:(i + 1) * v] = 1
                r += len(soles)
        self.checkpoint()
        self.report(0.65, "Smoothing and locking feet over %d frames" % n)
        calls = [0]

        def checked(flat: Any) -> Any:
            calls[0] += 1
            if calls[0] % 50 == 0:
                self.checkpoint()
            return residuals(flat)

        result = least_squares(checked, states.ravel(), jac_sparsity=sparsity.tocsr(), method="trf",
                               tr_solver="lsmr", loss="soft_l1", f_scale=1.0, x_scale="jac",
                               max_nfev=self.options.max_iterations)
        return result.x.reshape(n, v)

    # -------------------------------------------------------------- entry point
    def run(self) -> FitResult:
        import numpy as np  # type: ignore
        if not self.data:
            raise ValueError("no frames to fit (no frame has both a camera sample and a 2D pose)")
        states = self._solve_sequential()
        positions = self.model.positions(states)
        contacts, ground = self.detect_contacts(positions)
        if self.options.global_pass and len(self.data) >= 3:
            states = self._solve_global(states, contacts, ground if self.options.foot_lock else None)
            positions = self.model.positions(states)
        self.report(0.97, "Measuring the fit")
        errors = np.full((len(self.data), len(self.model.joints)), np.nan)
        uv, _ = self.project(positions, np.arange(len(self.data)))
        seen = self.obs_w > 0
        errors[seen] = np.linalg.norm(uv - self.obs_uv, axis=2)[seen]
        return FitResult(frames=[d.frame for d in self.data], joints=list(self.model.joints), positions=positions,
                         reprojection_px=errors, contacts={k: [bool(x) for x in v] for k, v in contacts.items()},
                         ground_height=ground)


def _moving_average(values: Any, window: int) -> Any:
    """Centred moving average along axis 0 (shorter windows at the ends)."""
    import numpy as np  # type: ignore
    out = np.empty_like(values, dtype=float)
    half = window // 2
    for i in range(len(values)):
        out[i] = values[max(0, i - half):i + half + 1].mean(axis=0)
    return out


def _min_runs(flags: Any, length: int) -> Any:
    """Drop runs of True shorter than ``length``."""
    import numpy as np  # type: ignore
    out = np.array(flags, dtype=bool)
    i = 0
    while i < len(out):
        if out[i]:
            j = i
            while j < len(out) and out[j]:
                j += 1
            if j - i < length:
                out[i:j] = False
            i = j
        else:
            i += 1
    return out
