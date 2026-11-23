"""Rotomation jobs (Phase 04).

* ``pose_track`` - follow one person through the plate: 2D body joints per frame (RTMPose or
  MediaPipe) and, optionally, 3D body joints from MediaPipe as the starting pose for the fit.
  Writes ``<output_dir>/<name>.pose.json``.
* ``roto_fit`` - fit the artist's rig proportions to that pose through their tracked Maya camera
  (camera + rig joints are read in Maya, query only, and written to a "fit input" JSON file).
  Writes ``<output_dir>/<name>.rotofit.json`` with world joint positions per frame.

The engine only writes these files. Applying them to the rig is an explicit, undoable command in Maya.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_engine.processors.base import BaseProcessor, JobContext
from ai_engine.processors.param_utils import opt_bool, opt_int, req_abs_path, req_name, req_str
from shared.contracts.errors import ContractError

POSE_SUFFIX = ".pose.json"
FIT_SUFFIX = ".rotofit.json"
POSE_TASKS = ("pose_2d",)


def _iou(a: List[float], b: List[float]) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _box(value: Any, key: str) -> List[float]:
    if (not isinstance(value, list) or len(value) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise ContractError("%s must be [x0, y0, x1, y1]" % key, key)
    x0, y0, x1, y1 = (float(v) for v in value)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ContractError("%s is too small" % key, key)
    return [x0, y0, x1, y1]


def _range(params: Dict[str, Any], out: Dict[str, Any]) -> None:
    out["start"] = opt_int(params, "start", None)
    out["end"] = opt_int(params, "end", None)
    if out["start"] is not None and out["end"] is not None and out["start"] > out["end"]:
        raise ContractError("start must be <= end", "start")


def _output(params: Dict[str, Any], out: Dict[str, Any], default_name: str) -> None:
    out["output_dir"] = req_abs_path(params, "output_dir")
    out["name"] = req_name(params, "name", default_name)
    out["overwrite"] = opt_bool(params, "overwrite", False)


def _check_overwrite(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError("%s already exists. Choose another name or allow overwrite." % path)


class _PoseModels:
    models: Any
    poses: Any

    def _model(self, params: Dict[str, Any], key: str, tasks: tuple, required: bool = True) -> Optional[str]:
        from ai_engine.models.model_manager import UnknownModel
        if not required and params.get(key) in (None, ""):
            return None
        model_id = req_str(params, key)
        try:
            info = self.models.get(model_id)
        except UnknownModel:
            raise ContractError("unknown model '%s'" % model_id, key)
        provides = {info.task} | set((info.options or {}).get("also", []))
        if not provides & set(tasks):
            raise ContractError("model '%s' cannot be used for %s" % (model_id, " / ".join(tasks)), key)
        if not info.installed:
            raise ContractError("model '%s' is not installed. Install it from the Model Manager." % model_id, key)
        return model_id


# ---------------------------------------------------------------------------------------- pose_track
class PoseTrackProcessor(_PoseModels, BaseProcessor):
    job_type = "pose_track"
    description = "Follow one person through the plate: 2D body joints (+ optional 3D body) per frame."

    def __init__(self, model_manager: Any, pose_cache: Any, settings: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(settings or {})
        self.models = model_manager
        self.poses = pose_cache

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        out: Dict[str, Any] = {"source": req_abs_path(params, "source", must_exist=True),
                               "first_frame": opt_int(params, "first_frame", 1001)}
        out["model_id"] = self._model(params, "model_id", POSE_TASKS)
        out["body3d_model_id"] = self._model(params, "body3d_model_id", ("body_3d",), required=False)
        out["detector_model_id"] = self._model(params, "detector_model_id", ("person_detection",), required=False)
        subject = params.get("subject")
        if subject is not None:
            if not isinstance(subject, dict):
                raise ContractError("subject must be {frame, box}", "subject")
            frame = opt_int(subject, "frame", None)
            if frame is None:
                raise ContractError("subject.frame is required", "subject")
            out["subject"] = {"frame": frame, "box": _box(subject.get("box"), "subject.box")}
        else:
            out["subject"] = None
        manifest = params.get("matte_manifest")
        out["matte_manifest"] = req_abs_path(params, "matte_manifest", must_exist=True) if manifest else None
        _range(params, out)
        _output(params, out, "body")
        return out

    @staticmethod
    def pose_path(output_dir: str, name: str) -> Path:
        return Path(output_dir) / (name + POSE_SUFFIX)

    @staticmethod
    def _matte_boxes(path: Optional[str]) -> Dict[int, List[float]]:
        if not path:
            return {}
        try:
            manifest = json.loads(Path(path).read_text(encoding="utf-8"))
            frames = manifest.get("frames") or {}
            return {int(f): [float(v) for v in info["bbox"]] for f, info in frames.items()
                    if isinstance(info, dict) and info.get("bbox")}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("cannot read the roto manifest %s: %s" % (path, exc))

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        from ai_engine.media.frame_source import open_source
        from ai_engine.pose import skeleton as sk
        from ai_engine.pose.backends import clip_box
        p = ctx.params
        target = self.pose_path(p["output_dir"], p["name"])
        _check_overwrite(target, p["overwrite"])
        mattes = self._matte_boxes(p["matte_manifest"])
        started = time.monotonic()
        ctx.report(0.01, "Loading pose models")
        with self.poses.inference_lock:
            pose = self.poses.get(p["model_id"])
            body3d = self.poses.get(p["body3d_model_id"]) if p["body3d_model_id"] else None
            detector = self.poses.get(p["detector_model_id"]) if p["detector_model_id"] else None
            with open_source(p["source"], p["first_frame"]) as source:
                info = source.info
                frames = [f for f in source.frames()
                          if (p["start"] is None or f >= p["start"]) and (p["end"] is None or f <= p["end"])]
                if not frames:
                    raise ValueError("no frames in the requested range")
                width, height = int(info.width), int(info.height)
                subject = p["subject"]
                if subject is not None and subject["frame"] not in frames:
                    raise ValueError("the subject frame %d is outside the frame range" % subject["frame"])
                seed = subject["frame"] if subject else next((f for f in frames if f in mattes), frames[0])
                order = [f for f in frames if f >= seed] + sorted((f for f in frames if f < seed), reverse=True)
                results: Dict[int, Dict[str, Any]] = {}
                previous_box: Optional[List[float]] = None
                backward_started = False
                for count, frame in enumerate(order):
                    ctx.checkpoint()
                    if frame < seed and not backward_started:
                        backward_started = True  # the backward pass starts again from the seed frame
                        previous_box = results[seed]["next_box"]
                    image = source.read(frame)
                    box, box_source = self._subject_box(frame, image, subject, mattes, detector, previous_box,
                                                        width, height)
                    body = pose.pose_2d(image, box)
                    entry: Dict[str, Any] = {"bbox": [round(v, 1) for v in box], "box_source": box_source,
                                             "kp2d": [[round(x, 2), round(y, 2), round(c, 3)] for x, y, c in body]}
                    if body3d is not None:
                        joints3d = body3d.body_3d(image, box)
                        entry["kp3d"] = ([[round(x, 4), round(y, 4), round(z, 4), round(c, 3)]
                                          for x, y, z, c in joints3d] if joints3d else None)
                    follow = sk.bbox_from_body(body)
                    entry["next_box"] = clip_box(follow, width, height) if follow else box
                    results[frame] = entry
                    previous_box = entry["next_box"]
                    ctx.report(0.03 + 0.95 * (count + 1) / len(order),
                               "Body pose frame %d (%d/%d)" % (frame, count + 1, len(order)))
        confidences = {f: _mean_conf(r["kp2d"]) for f, r in results.items()}
        weak = sorted(f for f, c in confidences.items() if c < 0.4)
        document = {
            "toolkit": "MayaAIProductionToolkit", "kind": "pose_track", "version": 1, "name": p["name"],
            "source": p["source"], "first_frame_offset": p["first_frame"],
            "media": {"width": width, "height": height}, "joints": sk.JOINTS,
            "models": {"pose_2d": p["model_id"], "body_3d": p["body3d_model_id"], "detector": p["detector_model_id"]},
            "subject": subject, "matte_manifest": p["matte_manifest"],
            "frames": {str(f): {k: v for k, v in results[f].items() if k != "next_box"} for f in sorted(results)},
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document), encoding="utf-8")
        return {"pose_file": str(target), "frames": len(results), "frame_range": [min(results), max(results)],
                "has_3d": body3d is not None, "weak_frames": weak[:500],
                "mean_confidence": round(sum(confidences.values()) / len(confidences), 3),
                "seconds": round(time.monotonic() - started, 2)}

    @staticmethod
    def _subject_box(frame: int, image: Any, subject: Optional[Dict[str, Any]], mattes: Dict[int, List[float]],
                     detector: Any, previous: Optional[List[float]], width: int, height: int) -> Any:
        from ai_engine.pose.backends import clip_box, expand_box
        if frame in mattes:
            box = expand_box(mattes[frame], 1.1, width, height)
            if box:
                return box, "matte"
        reference = previous
        if subject is not None and frame == subject["frame"]:
            reference = clip_box(subject["box"], width, height)
            if detector is None and reference:
                return reference, "artist"
        if detector is not None:
            detections = [b for b in (clip_box(d, width, height) for d in detector.detect(image)) if b]
            if detections:
                if reference is None:
                    best = max(detections, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    return best, "detector"
                best = max(detections, key=lambda b: _iou(b, reference))
                if _iou(best, reference) >= 0.1:
                    return best, "detector"
        if reference is not None:
            return reference, "previous"
        return [0.0, 0.0, width - 1.0, height - 1.0], "frame"


def _mean_conf(body: List[List[float]]) -> float:
    return sum(p[-1] for p in body) / float(len(body)) if body else 0.0


# ---------------------------------------------------------------------------------------- roto_fit
class RotoFitProcessor(BaseProcessor):
    job_type = "roto_fit"
    description = "Fit the artist's rig proportions to the body pose through their tracked Maya camera."

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from ai_engine.pose.fit import FitOptions
        params = super().validate_params(params)
        out: Dict[str, Any] = {"pose_file": req_abs_path(params, "pose_file", must_exist=True),
                               "fit_input": req_abs_path(params, "fit_input", must_exist=True)}
        try:
            out["options"] = FitOptions.from_dict(params.get("options")).to_dict()
        except ValueError as exc:
            raise ContractError(str(exc), "options")
        _range(params, out)
        _output(params, out, "rotofit")
        return out

    @staticmethod
    def fit_path(output_dir: str, name: str) -> Path:
        return Path(output_dir) / (name + FIT_SUFFIX)

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        import numpy as np  # type: ignore
        from ai_engine.pose import fit
        from ai_engine.pose.camera import camera_from_dict
        p = ctx.params
        target = self.fit_path(p["output_dir"], p["name"])
        _check_overwrite(target, p["overwrite"])
        started = time.monotonic()
        ctx.report(0.01, "Reading the pose and the Maya scene data")
        pose = _read_json(p["pose_file"], "pose file")
        scene = _read_json(p["fit_input"], "fit input")
        if pose.get("kind") != "pose_track" or scene.get("kind") != "roto_fit_input":
            raise ValueError("pose_file must come from pose_track and fit_input from Maya's rotomation panel")
        rig = scene.get("rig") or {}
        try:
            model = fit.BodyModel({k: v for k, v in (rig.get("joints") or {}).items()})
        except (TypeError, ValueError) as exc:
            raise ValueError("rig: %s" % exc)
        width, height = int(pose["media"]["width"]), int(pose["media"]["height"])
        cameras = (scene.get("camera") or {}).get("frames") or {}
        frames: List[fit.FrameData] = []
        skipped_no_camera = 0
        for key, entry in sorted(pose["frames"].items(), key=lambda kv: int(kv[0])):
            frame = int(key)
            if (p["start"] is not None and frame < p["start"]) or (p["end"] is not None and frame > p["end"]):
                continue
            sample = cameras.get(key)
            if sample is None:
                skipped_no_camera += 1
                continue
            prior = entry.get("kp3d")
            frames.append(fit.FrameData(
                frame=frame, camera=camera_from_dict(sample, width, height),
                obs=fit.select_joints(entry["kp2d"], model.joints),
                prior=fit.select_joints(prior, model.joints) if prior else None))
        if not frames:
            raise ValueError("no frame has both a body pose and a camera sample (check the Maya frame offset)")
        options = fit.FitOptions.from_dict(p["options"])
        options.up_axis = str(scene.get("up_axis", options.up_axis))
        result = fit.RotoFitter(model, frames, options, ctx.checkpoint, ctx.report).run()
        errors = result.reprojection_px
        per_frame_error = [float(np.nanmean(row)) if np.isfinite(row).any() else None for row in errors]
        threshold = max(8.0, 0.012 * height)
        flagged = [f for f, e in zip(result.frames, per_frame_error) if e is not None and e > threshold]
        document = {
            "toolkit": "MayaAIProductionToolkit", "kind": "roto_fit", "version": 1, "name": p["name"],
            "pose_file": p["pose_file"], "fit_input": p["fit_input"], "camera": (scene.get("camera") or {}).get("name"),
            "rig": {"name": rig.get("name"), "reference_frame": rig.get("reference_frame")},
            "up_axis": options.up_axis, "joints": result.joints, "options": options.to_dict(),
            "ground_height": result.ground_height,
            "frames": {str(f): {
                "p": [[round(float(v), 4) for v in joint] for joint in result.positions[i]],
                "err": [None if not np.isfinite(e) else round(float(e), 2) for e in errors[i]],
                "contact": {side: flags[i] for side, flags in result.contacts.items()},
            } for i, f in enumerate(result.frames)},
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document), encoding="utf-8")
        valid = [e for e in per_frame_error if e is not None]
        contacts = {side: sum(flags) for side, flags in result.contacts.items()}
        ctx.report(1.0, "Fit written")
        return {"fit_file": str(target), "frames": len(result.frames),
                "frame_range": [result.frames[0], result.frames[-1]],
                "mean_error_px": round(sum(valid) / len(valid), 2) if valid else None,
                "flagged_frames": flagged[:500], "flag_threshold_px": round(threshold, 1),
                "skipped_no_camera": skipped_no_camera, "contact_frames": contacts,
                "ground_height": result.ground_height, "seconds": round(time.monotonic() - started, 2)}


def _read_json(path: str, label: str) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read the %s %s: %s" % (label, path, exc))
    if not isinstance(data, dict):
        raise ValueError("the %s %s is not a JSON object" % (label, path))
    return data
