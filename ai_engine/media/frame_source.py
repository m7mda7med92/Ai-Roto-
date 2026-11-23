"""Frame sources: numbered image sequences and video files, read as RGB uint8 arrays."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")  # must be set before cv2 is imported

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp", ".dpx"}
VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".mxf", ".m4v", ".webm"}
_PADDING_RE = re.compile(r"(#+|%0?(\d*)d|\$F(\d*))")
_TRAILING_NUM_RE = re.compile(r"^(.*?)(\d+)$")


class MediaError(Exception):
    pass


def require_cv2() -> Any:
    try:
        import cv2  # type: ignore
        import numpy  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise MediaError("OpenCV/numpy are not installed in the engine environment (%s). "
                         "Re-run scripts/setup_engine.ps1." % exc)
    return cv2


@dataclass
class MediaInfo:
    path: str
    kind: str              # "sequence" | "video" | "image"
    first_frame: int
    last_frame: int
    width: int
    height: int
    fps: Optional[float] = None

    @property
    def frame_count(self) -> int:
        return self.last_frame - self.first_frame + 1

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "first_frame": self.first_frame, "last_frame": self.last_frame,
                "frame_count": self.frame_count, "width": self.width, "height": self.height, "fps": self.fps}


def to_rgb8(image: Any) -> Any:
    """Convert any OpenCV-decoded image (BGR/BGRA/gray, 8/16-bit or float) to RGB uint8."""
    import numpy as np  # type: ignore
    cv2 = require_cv2()
    if image is None:
        raise MediaError("image could not be decoded")
    if image.dtype == np.uint16:
        image = (image / 257.0).astype(np.uint8)
    elif image.dtype in (np.float32, np.float64, np.float16):
        # Scene-linear (EXR) -> display with a simple sRGB transfer; good enough for AI prompts/inference.
        linear = np.clip(image.astype(np.float32), 0.0, 1.0)
        srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
        image = (srgb * 255.0 + 0.5).astype(np.uint8)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class FrameSource:
    info: MediaInfo

    def read(self, frame: int) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def frames(self) -> List[int]:
        return list(range(self.info.first_frame, self.info.last_frame + 1))

    def close(self) -> None:
        pass

    def __enter__(self) -> "FrameSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ImageSequenceSource(FrameSource):
    """``shot.####.exr``, ``shot.%04d.png``, ``shot.$F4.jpg`` or any one file of a numbered sequence."""

    def __init__(self, path: str) -> None:
        self._files = resolve_sequence(path)
        if not self._files:
            raise MediaError("no frames found for %s" % path)
        first = self._read_file(self._files[min(self._files)])
        height, width = first.shape[:2]
        kind = "sequence" if len(self._files) > 1 else "image"
        self.info = MediaInfo(path, kind, min(self._files), max(self._files), width, height)

    def frames(self) -> List[int]:
        return sorted(self._files)

    def read(self, frame: int) -> Any:
        if frame not in self._files:
            raise MediaError("frame %d is missing from the sequence" % frame)
        return self._read_file(self._files[frame])

    @staticmethod
    def _read_file(path: Path) -> Any:
        cv2 = require_cv2()
        import numpy as np  # type: ignore
        data = np.fromfile(str(path), dtype=np.uint8)  # handles non-ASCII Windows paths
        image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if image is None:
            raise MediaError("cannot decode %s" % path)
        return to_rgb8(image)


class VideoSource(FrameSource):
    """Video file; frame numbers start at ``first_frame`` (default 1001, VFX convention)."""

    def __init__(self, path: str, first_frame: int = 1001) -> None:
        cv2 = require_cv2()
        self._cv2 = cv2
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            raise MediaError("cannot open video %s" % path)
        count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0) or None
        if count <= 0:
            raise MediaError("video %s reports no frames" % path)
        self._first = first_frame
        self._next_index = 0
        self.info = MediaInfo(str(path), "video", first_frame, first_frame + count - 1, width, height, fps)

    def read(self, frame: int) -> Any:
        index = frame - self._first
        if not 0 <= index < self.info.frame_count:
            raise MediaError("frame %d outside video range" % frame)
        if index != self._next_index:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self._cap.read()
        if not ok:
            raise MediaError("cannot read frame %d" % frame)
        self._next_index = index + 1
        return to_rgb8(image)

    def close(self) -> None:
        self._cap.release()


def resolve_sequence(path: str) -> Dict[int, Path]:
    """Map frame number -> file for a sequence pattern or a single member file."""
    p = Path(path)
    folder, name = p.parent, p.name
    stem, ext = os.path.splitext(name)
    match = _PADDING_RE.search(stem)
    if match:
        prefix, suffix = stem[: match.start()], stem[match.end():]
    else:
        trailing = _TRAILING_NUM_RE.match(stem)
        if not trailing:
            return {0: p} if p.is_file() else {}
        prefix, suffix = trailing.group(1), ""
    if not folder.is_dir():
        return {}
    pattern = re.compile("^" + re.escape(prefix) + r"(-?\d+)" + re.escape(suffix) + re.escape(ext) + "$", re.I)
    frames: Dict[int, Path] = {}
    for entry in folder.iterdir():
        found = pattern.match(entry.name)
        if found and entry.is_file():
            frames[int(found.group(1))] = entry
    return frames


def open_source(path: str, first_frame: int = 1001) -> FrameSource:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return VideoSource(path, first_frame)
    if ext in IMAGE_EXTS:
        return ImageSequenceSource(path)
    raise MediaError("unsupported media type: %s" % (ext or path))


def write_jpeg(path: Path, rgb: Any, max_size: int = 0, quality: int = 85) -> Tuple[int, int]:
    """Write an RGB uint8 array as JPEG, optionally downscaled so the longest side <= max_size."""
    cv2 = require_cv2()
    height, width = rgb.shape[:2]
    if max_size and max(width, height) > max_size:
        scale = max_size / float(max(width, height))
        rgb = cv2.resize(rgb, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise MediaError("JPEG encoding failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    data.tofile(str(path))
    return rgb.shape[1], rgb.shape[0]


def write_mask_png(path: Path, alpha: Any) -> None:
    """Write a single-channel uint8 matte as PNG."""
    cv2 = require_cv2()
    ok, data = cv2.imencode(".png", alpha)
    if not ok:
        raise MediaError("PNG encoding failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    data.tofile(str(path))
