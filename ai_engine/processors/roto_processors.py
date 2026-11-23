"""AI Roto jobs: viewer proxies, single-frame segmentation and mask tracking.

All paths are absolute and supplied by the client. The engine only writes:

* proxies / previews into the cache folder the client names (its own files, safe to refresh);
* final mattes into ``output_dir``. Existing mattes are never overwritten unless the request
  sets ``overwrite: true`` (the Maya UI asks the artist first).

Nothing here touches the Maya scene; Maya decides what to do with the written files.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from ai_engine.processors.base import BaseProcessor, JobContext
from ai_engine.processors.param_utils import opt_bool, opt_int, req_abs_path, req_name, req_str
from ai_engine.segmentation.prompts import Prompts
from ai_engine.segmentation.refine import RefineSettings, refine_mask
from shared.contracts.errors import ContractError

PROXY_PREFIX = "proxy"
PREVIEW_PREFIX = "preview"
DIRECTIONS = ("forward", "backward", "both")


def _common_media(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": req_abs_path(params, "source", must_exist=True),
            "first_frame": opt_int(params, "first_frame", 1001)}


def _bbox(mask: Any) -> Any:
    import numpy as np  # type: ignore
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


class _SegmentationMixin:
    """Shared model/prompt validation and backend access."""

    models: Any
    backends: Any

    def _validate_model(self, params: Dict[str, Any]) -> str:
        from ai_engine.models.model_manager import UnknownModel
        model_id = req_str(params, "model_id")
        try:
            info = self.models.get(model_id)
        except UnknownModel:
            raise ContractError("unknown model '%s'" % model_id, "model_id")
        if not info.installed:
            raise ContractError("model '%s' is not installed. Install it from the Model Manager." % model_id,
                                "model_id")
        return model_id

    @staticmethod
    def _validate_prompts(params: Dict[str, Any]) -> Prompts:
        return Prompts.from_dict(params.get("prompts"))

    @contextmanager
    def _backend(self, ctx: JobContext, model_id: str) -> Iterator[Any]:
        ctx.report(0.02, "Loading model %s" % model_id)
        backend = self.backends.get(model_id)
        if backend.name == "classical":
            yield backend
            return
        if not self.backends.inference_lock.acquire(blocking=False):
            ctx.report(0.02, "Waiting for the GPU (another AI job is running)")
            while not self.backends.inference_lock.acquire(timeout=0.2):
                ctx.checkpoint()
        try:
            yield backend
        finally:
            self.backends.inference_lock.release()


# ------------------------------------------------------------------------------------------ proxies
class MediaProxiesProcessor(BaseProcessor):
    job_type = "media_proxies"
    description = "Probe a plate (sequence or video) and write JPEG proxies for the Maya Roto viewer."

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        out = _common_media(params)
        out["output_dir"] = req_abs_path(params, "output_dir")
        out["max_size"] = opt_int(params, "max_size", int(self.settings.get("proxy_max_size", 1280)), 64, 8192)
        out["quality"] = opt_int(params, "quality", int(self.settings.get("proxy_quality", 85)), 10, 100)
        out["start"] = opt_int(params, "start", None)
        out["end"] = opt_int(params, "end", None)
        return out

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        from ai_engine.media.frame_source import open_source, write_jpeg
        p = ctx.params
        out_dir = Path(p["output_dir"])
        ctx.report(0.0, "Opening %s" % os.path.basename(p["source"]))
        with open_source(p["source"], p["first_frame"]) as source:
            info = source.info
            frames = [f for f in source.frames()
                      if (p["start"] is None or f >= p["start"]) and (p["end"] is None or f <= p["end"])]
            if not frames:
                raise ValueError("no frames in the requested range")
            source_mtime = os.path.getmtime(p["source"]) if os.path.isfile(p["source"]) else 0.0
            proxy_size = None
            written = skipped = 0
            for index, frame in enumerate(frames):
                ctx.checkpoint()
                target = out_dir / ("%s.%04d.jpg" % (PROXY_PREFIX, frame))
                # Reuse a proxy that is newer than the plate (cheap cache for re-opened shots).
                if target.is_file() and proxy_size and target.stat().st_mtime >= source_mtime:
                    skipped += 1
                else:
                    width, height = write_jpeg(target, source.read(frame), p["max_size"], p["quality"])
                    proxy_size = proxy_size or [width, height]
                    written += 1
                ctx.report((index + 1) / len(frames), "Proxy %d/%d (frame %d)" % (index + 1, len(frames), frame))
        return {"media": info.to_dict(), "frames": frames, "proxy_dir": str(out_dir),
                "proxy_pattern": "%s.####.jpg" % PROXY_PREFIX, "proxy_size": proxy_size,
                "written": written, "reused": skipped}


# ------------------------------------------------------------------------------------------ segment
class SegmentFrameProcessor(_SegmentationMixin, BaseProcessor):
    job_type = "segment_frame"
    description = "Segment one frame from click/box prompts and write a preview matte."

    def __init__(self, model_manager: Any, backend_cache: Any, settings: Dict[str, Any] = None) -> None:
        super().__init__(settings or {})
        self.models = model_manager
        self.backends = backend_cache

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        out = _common_media(params)
        out["model_id"] = self._validate_model(params)
        out["frame"] = opt_int(params, "frame", None)
        if out["frame"] is None:
            raise ContractError("frame is required", "frame")
        out["prompts"] = self._validate_prompts(params).to_dict()
        out["refine"] = RefineSettings.from_dict(params.get("refine")).to_dict()
        out["preview_dir"] = req_abs_path(params, "preview_dir")
        return out

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        from ai_engine.media.frame_source import open_source, write_mask_png
        p = ctx.params
        with open_source(p["source"], p["first_frame"]) as source:
            image = source.read(p["frame"])
        height, width = image.shape[:2]
        prompts = Prompts.from_dict(p["prompts"], width, height)
        started = time.monotonic()
        with self._backend(ctx, p["model_id"]) as backend:
            ctx.checkpoint()
            ctx.report(0.3, "Segmenting frame %d with %s" % (p["frame"], backend.name))
            mask = backend.segment(image, prompts)
        alpha = refine_mask(mask, RefineSettings.from_dict(p["refine"]))
        target = Path(p["preview_dir"]) / ("%s.%04d.png" % (PREVIEW_PREFIX, p["frame"]))
        write_mask_png(target, alpha)
        ctx.report(1.0, "Mask ready")
        return {"frame": p["frame"], "mask_path": str(target), "width": width, "height": height,
                "coverage": round(float((alpha > 127).mean()), 5), "bbox": _bbox(alpha > 127),
                "backend": backend.name, "seconds": round(time.monotonic() - started, 3)}


# ------------------------------------------------------------------------------------------ track
class RotoTrackProcessor(_SegmentationMixin, BaseProcessor):
    job_type = "roto_track"
    description = "Track a prompted mask across a frame range and write a matte sequence + manifest."

    def __init__(self, model_manager: Any, backend_cache: Any, settings: Dict[str, Any] = None) -> None:
        super().__init__(settings or {})
        self.models = model_manager
        self.backends = backend_cache

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        out = _common_media(params)
        out["model_id"] = self._validate_model(params)
        out["prompt_frame"] = opt_int(params, "prompt_frame", None)
        if out["prompt_frame"] is None:
            raise ContractError("prompt_frame is required", "prompt_frame")
        out["prompts"] = self._validate_prompts(params).to_dict()
        out["start"] = opt_int(params, "start", None)
        out["end"] = opt_int(params, "end", None)
        if out["start"] is not None and out["end"] is not None and out["start"] > out["end"]:
            raise ContractError("start must be <= end", "start")
        for key in ("start", "end"):
            value = out[key]
            if value is not None and ((key == "start" and value > out["prompt_frame"])
                                      or (key == "end" and value < out["prompt_frame"])):
                raise ContractError("the prompt frame must lie inside the frame range", key)
        direction = params.get("direction", "both")
        if direction not in DIRECTIONS:
            raise ContractError("direction must be one of %s" % ", ".join(DIRECTIONS), "direction")
        out["direction"] = direction
        out["refine"] = RefineSettings.from_dict(params.get("refine")).to_dict()
        out["output_dir"] = req_abs_path(params, "output_dir")
        out["name"] = req_name(params, "name", "roto")
        out["overwrite"] = opt_bool(params, "overwrite", False)
        return out

    @staticmethod
    def matte_path(output_dir: str, name: str, frame: int) -> Path:
        return Path(output_dir) / ("%s.%04d.png" % (name, frame))

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        from ai_engine.media.frame_source import open_source, write_mask_png
        p = ctx.params
        refine = RefineSettings.from_dict(p["refine"])
        started = time.monotonic()
        with open_source(p["source"], p["first_frame"]) as source:
            all_frames = source.frames()
            if p["prompt_frame"] not in all_frames:
                raise ValueError("prompt frame %d is not in the plate (%d-%d)"
                                 % (p["prompt_frame"], all_frames[0], all_frames[-1]))
            in_range = [f for f in all_frames
                        if (p["start"] is None or f >= p["start"]) and (p["end"] is None or f <= p["end"])]
            forward = [f for f in in_range if f >= p["prompt_frame"]]
            backward = sorted((f for f in in_range if f <= p["prompt_frame"]), reverse=True)
            passes: List[List[int]] = []
            if p["direction"] in ("forward", "both") and forward:
                passes.append(forward)
            if p["direction"] in ("backward", "both") and len(backward) > (1 if passes else 0):
                passes.append(backward)
            targets = sorted({f for frames in passes for f in frames})
            existing = [f for f in targets if self.matte_path(p["output_dir"], p["name"], f).exists()]
            if existing and not p["overwrite"]:
                raise FileExistsError("%d matte(s) named '%s' already exist in %s (e.g. frame %d). "
                                      "Choose another name or allow overwrite."
                                      % (len(existing), p["name"], p["output_dir"], existing[0]))
            image = source.read(p["prompt_frame"])
            prompts = Prompts.from_dict(p["prompts"], image.shape[1], image.shape[0])
            done: Dict[int, Dict[str, Any]] = {}
            total = max(1, len(targets))

            def sink(frame: int, mask: Any) -> None:
                if frame in done:
                    return  # the prompt frame is produced by both passes; keep the first
                alpha = refine_mask(mask, refine)
                write_mask_png(self.matte_path(p["output_dir"], p["name"], frame), alpha)
                done[frame] = {"coverage": round(float((alpha > 127).mean()), 5), "bbox": _bbox(alpha > 127)}
                ctx.report(0.05 + 0.95 * len(done) / total, "Tracked frame %d (%d/%d)" % (frame, len(done), total))

            with self._backend(ctx, p["model_id"]) as backend:
                for frames in passes:
                    ctx.checkpoint()
                    backend.track(source.read, frames, p["prompt_frame"], prompts, sink, ctx.checkpoint)
        manifest = {
            "toolkit": "MayaAIProductionToolkit", "kind": "roto_matte_sequence", "version": 1,
            "name": p["name"], "source": p["source"], "first_frame_offset": p["first_frame"],
            "model_id": p["model_id"], "backend": backend.name, "prompt_frame": p["prompt_frame"],
            "prompts": p["prompts"], "refine": p["refine"], "direction": p["direction"],
            "frame_range": [targets[0], targets[-1]] if targets else None,
            "pattern": "%s.####.png" % p["name"], "frames": {str(k): v for k, v in sorted(done.items())},
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        manifest_path = Path(p["output_dir"]) / ("%s.roto.json" % p["name"])
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"output_dir": p["output_dir"], "pattern": manifest["pattern"], "manifest": str(manifest_path),
                "frames_written": len(done), "frame_range": manifest["frame_range"], "backend": backend.name,
                "seconds": round(time.monotonic() - started, 3)}
