"""Pure helpers for Roto Studio: folder conventions, prompt state and engine job requests.

No Qt and no Maya imports, so everything here is unit-testable outside Maya.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from maya_plugin.communication.contracts import JobRequest, make_job_request
from shared.paths import user_dir

MEDIA_PROXIES_JOB = "media_proxies"
SEGMENT_FRAME_JOB = "segment_frame"
ROTO_TRACK_JOB = "roto_track"
SEGMENTATION_TASK = "segmentation"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def valid_matte_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def cache_root(project: Any = None) -> Path:
    """Toolkit cache folder: ``<project>/cache`` when a project is open, else ``<user dir>/cache``."""
    if project is not None:
        return Path(project.root) / project.paths.get("cache", "cache")
    return user_dir() / "cache"


def proxy_dir(source: str, project: Any = None) -> Path:
    """Per-plate proxy folder; keyed on the normalised source path so re-opening reuses proxies."""
    key = hashlib.sha1(os.path.normcase(os.path.normpath(source)).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", Path(source).stem.replace("#", ""))[:40].strip("_") or "plate"
    return cache_root(project) / "roto_proxies" / ("%s_%s" % (stem, key))


def preview_dir(source: str, project: Any = None) -> Path:
    return proxy_dir(source, project) / "preview"


def default_output_dir(name: str, project: Any = None) -> Optional[Path]:
    """``<project>/roto/<name>``; ``None`` without a project (the artist must choose a folder)."""
    if project is None:
        return None
    return Path(project.root) / project.paths.get("roto", "roto") / name


def matte_path(output_dir: str, name: str, frame: int) -> Path:
    return Path(output_dir) / ("%s.%04d.png" % (name, frame))


def existing_mattes(output_dir: str, name: str, frames: List[int]) -> List[int]:
    """Frames whose matte file already exists (the UI asks before overwriting them)."""
    return [f for f in frames if matte_path(output_dir, name, f).exists()]


def frames_for_track(frames: List[int], prompt_frame: int, start: int, end: int, direction: str) -> List[int]:
    """Mirror of the engine's frame selection, used for the overwrite check."""
    in_range = [f for f in frames if start <= f <= end]
    if direction == "forward":
        return [f for f in in_range if f >= prompt_frame]
    if direction == "backward":
        return [f for f in in_range if f <= prompt_frame]
    return in_range


@dataclass
class PromptState:
    """Prompts the artist placed on one frame, in *source* pixel coordinates."""

    frame: Optional[int] = None
    points: List[Tuple[float, float, int]] = field(default_factory=list)
    box: Optional[Tuple[float, float, float, float]] = None

    def is_empty(self) -> bool:
        return not self.points and self.box is None

    def is_usable(self) -> bool:
        return self.box is not None or any(label == 1 for _, _, label in self.points)

    def add_point(self, frame: int, x: float, y: float, include: bool) -> None:
        self._retarget(frame)
        self.points.append((float(x), float(y), 1 if include else 0))

    def set_box(self, frame: int, x0: float, y0: float, x1: float, y1: float) -> None:
        self._retarget(frame)
        self.box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def undo(self) -> None:
        if self.points:
            self.points.pop()
        elif self.box is not None:
            self.box = None

    def clear(self) -> None:
        self.frame, self.points, self.box = None, [], None

    def _retarget(self, frame: int) -> None:
        if self.frame != frame:  # prompts belong to a single frame
            self.points, self.box = [], None
            self.frame = frame

    def to_dict(self) -> Dict[str, Any]:
        return {"points": [[round(x, 2), round(y, 2), label] for x, y, label in self.points],
                "box": [round(v, 2) for v in self.box] if self.box else None}


def proxies_request(source: str, first_frame: int, output_dir: str, max_size: int = 1280) -> JobRequest:
    return make_job_request(MEDIA_PROXIES_JOB, {"source": source, "first_frame": first_frame,
                                                "output_dir": output_dir, "max_size": max_size},
                            "Load plate %s" % os.path.basename(source))


def segment_request(source: str, first_frame: int, frame: int, model_id: str, prompts: PromptState,
                    refine: Dict[str, Any], out_dir: str) -> JobRequest:
    return make_job_request(SEGMENT_FRAME_JOB, {
        "source": source, "first_frame": first_frame, "frame": frame, "model_id": model_id,
        "prompts": prompts.to_dict(), "refine": dict(refine), "preview_dir": out_dir,
    }, "Segment frame %d" % frame)


def track_request(source: str, first_frame: int, model_id: str, prompts: PromptState, start: int, end: int,
                  direction: str, refine: Dict[str, Any], output_dir: str, name: str,
                  overwrite: bool = False) -> JobRequest:
    return make_job_request(ROTO_TRACK_JOB, {
        "source": source, "first_frame": first_frame, "model_id": model_id, "prompt_frame": prompts.frame,
        "prompts": prompts.to_dict(), "start": start, "end": end, "direction": direction,
        "refine": dict(refine), "output_dir": output_dir, "name": name, "overwrite": overwrite,
    }, "Track mask '%s' %d-%d" % (name, start, end))
