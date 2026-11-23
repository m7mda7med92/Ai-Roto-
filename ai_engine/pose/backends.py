"""Pose model backends (Phase 04).

* :class:`RtmPoseBackend` - RTMPose (OpenMMLab) top-down 2D keypoints through ONNX Runtime, using
  ``rtmlib`` for the pre/post-processing. GPU through CUDA when ``onnxruntime-gpu`` is installed.
* :class:`YoloxDetector` - optional YOLOX person detector (also via ``rtmlib``).
* :class:`MediaPipeBackend` - MediaPipe Pose Landmarker: 2D keypoints *and* 3D body joints.

All backends work on one subject at a time: the caller passes the subject's box on that frame (the
box comes from the artist, a roto matte, the detector or the previous frame's pose). Outputs use the
toolkit's body joints (see :mod:`ai_engine.pose.skeleton`).

Models are loaded once by :class:`PoseBackendCache` and reused across jobs.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ai_engine.models.model_manager import extracted_dir, find_file
from ai_engine.pose import skeleton as sk

log = logging.getLogger("ai_engine.pose")

Box = List[float]


class PoseUnavailable(Exception):
    """The backend's Python packages or weights are missing."""


def clip_box(box: Sequence[float], width: int, height: int) -> Optional[Box]:
    x0, y0, x1, y1 = (float(v) for v in box)
    x0, y0 = max(0.0, min(x0, x1)), max(0.0, min(y0, y1))
    x1, y1 = min(width - 1.0, max(box[0], box[2])), min(height - 1.0, max(box[1], box[3]))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return [x0, y0, x1, y1]


def expand_box(box: Sequence[float], factor: float, width: int, height: int, square: bool = False) -> Optional[Box]:
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    w, h = (box[2] - box[0]) * factor, (box[3] - box[1]) * factor
    if square:
        w = h = max(w, h)
    return clip_box([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], width, height)


def onnx_device(device: str) -> str:
    """'auto' -> 'cuda' when ONNX Runtime has the CUDA provider, else 'cpu'."""
    try:
        import onnxruntime  # type: ignore
    except ImportError:
        return "cpu"
    if hasattr(onnxruntime, "preload_dlls"):
        try:  # CUDA / cuDNN DLLs from the nvidia-* pip packages (onnxruntime-gpu[cuda,cudnn])
            onnxruntime.preload_dlls()
        except Exception:  # pragma: no cover - depends on the machine
            log.debug("onnxruntime.preload_dlls failed", exc_info=True)
    if device != "auto":
        return device
    return "cuda" if "CUDAExecutionProvider" in onnxruntime.get_available_providers() else "cpu"


def _onnx_tool(factory: Any, device: str, **kwargs: Any) -> Any:
    """Create an rtmlib tool on the GPU, falling back to the CPU if the CUDA session cannot start."""
    chosen = onnx_device(device)
    try:
        return factory(backend="onnxruntime", device=chosen, **kwargs), chosen
    except Exception as exc:
        if chosen == "cpu":
            raise
        log.warning("ONNX Runtime CUDA session failed, using the CPU: %s", exc)
        return factory(backend="onnxruntime", device="cpu", **kwargs), "cpu"


class PoseBackend:
    """Interface. ``pose_2d`` and ``body_3d`` take an RGB uint8 frame and the subject's box."""

    name = "base"
    provides: Tuple[str, ...] = ()

    def pose_2d(self, image: Any, box: Box) -> List[List[float]]:
        """Body joints ``[[x, y, conf]] * 17`` in plate pixels."""
        raise NotImplementedError

    def body_3d(self, image: Any, box: Box) -> Optional[List[List[float]]]:
        """Body joints ``[[x, y, z, conf]] * 17`` in Maya camera space, or ``None`` if no body found."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class PersonDetector:
    name = "base"

    def detect(self, image: Any) -> List[Box]:
        raise NotImplementedError


# ---------------------------------------------------------------------------------------- RTMPose
def _onnx_in(folder: Path, zip_name: str) -> Path:
    found = find_file(folder / extracted_dir(zip_name), ".onnx") or find_file(folder, ".onnx")
    if found is None:
        raise PoseUnavailable("no .onnx file found in %s (re-install the model)" % folder)
    return found


class RtmPoseBackend(PoseBackend):
    name = "rtmpose"
    provides = ("pose_2d",)

    def __init__(self, model_path: Path, input_size: Sequence[int], layout: str, device: str = "auto") -> None:
        try:
            from rtmlib import RTMPose  # type: ignore
        except ImportError as exc:
            raise PoseUnavailable("RTMPose needs 'rtmlib' and 'onnxruntime(-gpu)' in the engine environment "
                                  "(scripts/setup_engine.ps1 -WithPose): %s" % exc)
        folder = Path(model_path).parent
        onnx = _onnx_in(folder, Path(model_path).name)
        self.layout = layout
        self._model, self.device = _onnx_tool(RTMPose, device, onnx_model=str(onnx),
                                              model_input_size=tuple(int(v) for v in input_size))
        log.info("Loaded RTMPose", extra={"model": str(onnx), "device": self.device})

    def pose_2d(self, image: Any, box: Box) -> List[List[float]]:
        import numpy as np  # type: ignore
        bgr = np.ascontiguousarray(image[:, :, ::-1])
        keypoints, scores = self._model(bgr, bboxes=[list(box)])
        return sk.to_body(keypoints[0].tolist(), scores[0].tolist(), self.layout)


class YoloxDetector(PersonDetector):
    name = "yolox"

    def __init__(self, model_path: Path, input_size: Sequence[int], device: str = "auto") -> None:
        try:
            from rtmlib import YOLOX  # type: ignore
        except ImportError as exc:
            raise PoseUnavailable("the person detector needs 'rtmlib' and 'onnxruntime(-gpu)' "
                                  "(scripts/setup_engine.ps1 -WithPose): %s" % exc)
        onnx = _onnx_in(Path(model_path).parent, Path(model_path).name)
        self._model, self.device = _onnx_tool(YOLOX, device, onnx_model=str(onnx),
                                              model_input_size=tuple(int(v) for v in input_size))

    def detect(self, image: Any) -> List[Box]:
        import numpy as np  # type: ignore
        boxes = self._model(np.ascontiguousarray(image[:, :, ::-1]))
        return [[float(v) for v in b[:4]] for b in (boxes if boxes is not None else [])]


# ---------------------------------------------------------------------------------------- MediaPipe
class MediaPipeBackend(PoseBackend):
    """Pose Landmarker on a crop around the subject (so it always picks the artist's subject)."""

    name = "mediapipe"
    provides = ("pose_2d", "body_3d")
    CROP_PAD = 1.35

    def __init__(self, model_path: Path) -> None:
        try:
            import mediapipe as mp  # type: ignore
            from mediapipe.tasks.python import BaseOptions, vision  # type: ignore
        except ImportError as exc:
            raise PoseUnavailable("MediaPipe is not installed in the engine environment "
                                  "(scripts/setup_engine.ps1 -WithPose): %s" % exc)
        if not Path(model_path).is_file():
            raise PoseUnavailable("MediaPipe model not found: %s" % model_path)
        self._mp = mp
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)), running_mode=vision.RunningMode.IMAGE,
            num_poses=1, min_pose_detection_confidence=0.3, min_pose_presence_confidence=0.3)
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._cache: Tuple[Any, Any] = (None, None)  # (key, result) so 2D + 3D on one frame run once

    def _run(self, image: Any, box: Box) -> Tuple[Any, Optional[Box]]:
        import numpy as np  # type: ignore
        key = (id(image), tuple(box))
        if self._cache[0] == key:
            return self._cache[1]
        h, w = image.shape[:2]
        crop_box = expand_box(box, self.CROP_PAD, w, h, square=True)
        result: Tuple[Any, Optional[Box]] = (None, crop_box)
        if crop_box is not None:
            x0, y0, x1, y1 = (int(round(v)) for v in crop_box)
            crop = np.ascontiguousarray(image[y0:y1 + 1, x0:x1 + 1])
            detected = self._landmarker.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=crop))
            if detected.pose_landmarks:
                result = (detected, [float(x0), float(y0), float(x1 + 1 - x0), float(y1 + 1 - y0)])
        self._cache = (key, result)
        return result

    def pose_2d(self, image: Any, box: Box) -> List[List[float]]:
        detected, crop = self._run(image, box)
        if detected is None or crop is None:
            return [[0.0, 0.0, 0.0] for _ in sk.JOINTS]
        x0, y0, cw, ch = crop
        landmarks = detected.pose_landmarks[0]
        points = [[x0 + lm.x * cw, y0 + lm.y * ch] for lm in landmarks]
        scores = [min(getattr(lm, "visibility", 1.0) or 0.0, getattr(lm, "presence", 1.0) or 1.0) for lm in landmarks]
        return sk.to_body(points, scores, "mediapipe33")

    def body_3d(self, image: Any, box: Box) -> Optional[List[List[float]]]:
        detected, _crop = self._run(image, box)
        if detected is None or not detected.pose_world_landmarks:
            return None
        world = detected.pose_world_landmarks[0]
        # MediaPipe world: x = image right, y = down, z = towards the camera is smaller.
        # Maya camera space: x right, y up, camera looks down -Z (closer = larger z).
        points = [[lm.x, -lm.y, -lm.z] for lm in world]
        scores = [getattr(lm, "visibility", 1.0) or 0.0 for lm in world]
        return sk.to_body(points, scores, "mediapipe33")

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:  # pragma: no cover - mediapipe teardown quirks
            pass


# ---------------------------------------------------------------------------------------- cache
class PoseBackendCache:
    """Creates pose backends on demand and keeps them loaded (they are small)."""

    def __init__(self, model_manager: Any, device: str = "auto") -> None:
        self._models = model_manager
        self._device = device
        self._lock = threading.Lock()
        self._loaded: Dict[str, Any] = {}
        #: serialises pose inference (ONNX Runtime / MediaPipe sessions are not shared across jobs)
        self.inference_lock = threading.Lock()

    def get(self, model_id: str) -> Any:
        with self._lock:
            if model_id in self._loaded:
                return self._loaded[model_id]
            info = self._models.get(model_id)
            if not info.installed:
                raise PoseUnavailable("model %s is not installed" % model_id)
            path = self._models.file_path(model_id)
            options = info.options or {}
            if info.backend == "rtmpose":
                backend: Any = RtmPoseBackend(path, options.get("input_size", [192, 256]),
                                              options.get("layout", "halpe26"), self._device)
            elif info.backend == "yolox":
                backend = YoloxDetector(path, options.get("input_size", [640, 640]), self._device)
            elif info.backend == "mediapipe":
                backend = MediaPipeBackend(path)
            else:
                raise PoseUnavailable("unknown pose backend %s" % info.backend)
            self._loaded[model_id] = backend
            return backend

    def evict(self, model_id: str) -> None:
        with self._lock:
            backend = self._loaded.pop(model_id, None)
        if backend is not None and hasattr(backend, "close"):
            backend.close()

    def close_all(self) -> None:
        with self._lock:
            loaded, self._loaded = list(self._loaded.values()), {}
        for backend in loaded:
            if hasattr(backend, "close"):
                try:
                    backend.close()
                except Exception:  # pragma: no cover - best effort at shutdown
                    log.warning("Closing a pose model failed", exc_info=True)

    def register(self, model_id: str, backend: Any) -> None:
        """Tests and future adapters can plug a backend in directly."""
        with self._lock:
            self._loaded[model_id] = backend
