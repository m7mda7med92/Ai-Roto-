"""Segmentation backends.

``SegmentationBackend`` is the seam between roto jobs and AI models:

* :class:`ClassicalBackend` - OpenCV GrabCut + optical-flow propagation. CPU only,
  no weights. Always available; used for tests and as a fallback.
* :class:`Sam2Backend` - Meta SAM 2.1 (``sam2`` + ``torch``), image predictor for a
  single frame and video predictor for propagation. Needs installed weights; CUDA
  strongly recommended.

Backends are cached by :class:`BackendCache` so a model is loaded once and reused
across jobs; GPU inference is serialised with a lock.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_engine.segmentation.prompts import Prompts

log = logging.getLogger("ai_engine.segmentation")

#: (frame_number, bool mask HxW) callback used while tracking
MaskSink = Callable[[int, Any], None]


class BackendUnavailable(Exception):
    """The backend's Python dependencies or weights are missing."""


class SegmentationBackend:
    name = "base"

    def segment(self, image: Any, prompts: Prompts) -> Any:
        """Return a boolean HxW mask for an RGB uint8 image."""
        raise NotImplementedError

    def track(self, read_frame: Callable[[int], Any], frames: List[int], prompt_frame: int, prompts: Prompts,
              sink: MaskSink, checkpoint: Callable[[], None], guides: Optional[Dict[int, Prompts]] = None) -> None:
        """Segment ``prompt_frame`` and propagate through ``frames`` (ordered, starting at ``prompt_frame``).

        ``guides`` (Phase 03) maps frame -> extra prompts on later frames, typically projected from the
        artist's own tracking in Maya. Backends use them to re-anchor the mask on those frames.
        """
        raise NotImplementedError


def box_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


# ---------------------------------------------------------------------------------------- classical
class ClassicalBackend(SegmentationBackend):
    """GrabCut segmentation with Farneback optical-flow mask propagation (CPU)."""

    name = "classical"
    WORK_SIZE = 960       # longest side used for processing
    ITERATIONS = 5

    def __init__(self) -> None:
        try:
            import cv2  # type: ignore  # noqa: F401
            import numpy  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise BackendUnavailable("OpenCV/numpy not installed: %s" % exc)

    # -------------------------------------------------------------- helpers
    def _scale(self, image: Any) -> float:
        h, w = image.shape[:2]
        return min(1.0, self.WORK_SIZE / float(max(h, w)))

    @staticmethod
    def _resize(image: Any, scale: float, nearest: bool = False) -> Any:
        import cv2  # type: ignore
        if scale == 1.0:
            return image
        h, w = image.shape[:2]
        return cv2.resize(image, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                          interpolation=cv2.INTER_NEAREST if nearest else cv2.INTER_AREA)

    @staticmethod
    def _largest_components(mask: Any, keep_points: List[Tuple[int, int]]) -> Any:
        """Drop small speckles; keep components containing a positive point (or the largest one)."""
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        if count <= 2:
            return mask.astype(bool)
        keep = {labels[y, x] for x, y in keep_points if 0 <= y < labels.shape[0] and 0 <= x < labels.shape[1]}
        keep.discard(0)
        if not keep:
            keep = {1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))}
        return np.isin(labels, list(keep))

    def _grabcut(self, image: Any, init: Any, rect: Optional[Tuple[int, int, int, int]]) -> Any:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        mask = init.copy()
        mode = cv2.GC_INIT_WITH_MASK
        if rect is not None and not np.isin(mask, (cv2.GC_FGD, cv2.GC_BGD)).any() and (mask == cv2.GC_PR_FGD).sum() == 0:
            mode = cv2.GC_INIT_WITH_RECT
        if mode == cv2.GC_INIT_WITH_MASK and not np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).any():
            return np.zeros(mask.shape, bool)
        if mode == cv2.GC_INIT_WITH_MASK and not np.isin(mask, (cv2.GC_BGD, cv2.GC_PR_BGD)).any():
            return np.ones(mask.shape, bool)
        cv2.grabCut(bgr, mask, rect if mode == cv2.GC_INIT_WITH_RECT else None, bgd, fgd, self.ITERATIONS, mode)
        return np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD))

    # -------------------------------------------------------------- API
    def segment(self, image: Any, prompts: Prompts) -> Any:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        scale = self._scale(image)
        small = self._resize(image, scale)
        h, w = small.shape[:2]
        init = np.full((h, w), cv2.GC_BGD, np.uint8)
        radius = max(3, int(min(h, w) * 0.012))
        if prompts.box is not None:
            x0, y0, x1, y1 = (int(round(v * scale)) for v in prompts.box)
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1)
        else:
            xs = [x * scale for x, _ in prompts.positive]
            ys = [y * scale for _, y in prompts.positive]
            pad = 0.25 * max(w, h) if len(xs) == 1 else 0.15 * max(max(xs) - min(xs), max(ys) - min(ys), 40)
            x0, y0 = int(max(0, min(xs) - pad)), int(max(0, min(ys) - pad))
            x1, y1 = int(min(w - 1, max(xs) + pad)), int(min(h - 1, max(ys) + pad))
        init[y0:y1 + 1, x0:x1 + 1] = cv2.GC_PR_FGD
        for x, y in prompts.positive:
            cv2.circle(init, (int(x * scale), int(y * scale)), radius, int(cv2.GC_FGD), -1)
        for x, y in prompts.negative:
            cv2.circle(init, (int(x * scale), int(y * scale)), radius, int(cv2.GC_BGD), -1)
        rect = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        mask = self._grabcut(small, init, rect if not prompts.points else None)
        keep = [(int(x * scale), int(y * scale)) for x, y in prompts.positive]
        mask = self._largest_components(mask, keep)
        if scale != 1.0:
            mask = cv2.resize(mask.astype(np.uint8), (image.shape[1], image.shape[0]),
                              interpolation=cv2.INTER_LINEAR) > 0
        return mask

    @staticmethod
    def _agrees(mask: Any, guide: Prompts, scale: float) -> bool:
        """Does a (work-resolution) mask still sit on the guide? Used to recover after drift/occlusion."""
        import numpy as np  # type: ignore
        if not mask.any():
            return False
        h, w = mask.shape
        inside = [mask[min(h - 1, int(y * scale)), min(w - 1, int(x * scale))] for x, y in guide.positive]
        if inside and sum(bool(v) for v in inside) < 0.5 * len(inside):
            return False
        if guide.box is not None:
            ys, xs = np.nonzero(mask)
            iou = box_iou((xs.min(), ys.min(), xs.max(), ys.max()), tuple(v * scale for v in guide.box))
            if iou < 0.3:
                return False
        return True

    @staticmethod
    def _apply_guide(init: Any, guide: Prompts, scale: float, radius: int) -> None:
        """Constrain a GrabCut init with guide prompts: outside the (padded) box is background."""
        import cv2  # type: ignore
        h, w = init.shape
        if guide.box is not None:
            x0, y0, x1, y1 = (v * scale for v in guide.box)
            pad = 0.12 * max(x1 - x0, y1 - y0) + radius
            x0, y0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
            x1, y1 = int(min(w - 1, x1 + pad)), int(min(h - 1, y1 + pad))
            outside = init.copy()
            outside[y0:y1 + 1, x0:x1 + 1] = 255
            init[outside != 255] = cv2.GC_BGD
            region = init[y0:y1 + 1, x0:x1 + 1]
            region[region == cv2.GC_BGD] = cv2.GC_PR_BGD  # let GrabCut grow inside the guide box
        for x, y in guide.positive:
            cv2.circle(init, (int(x * scale), int(y * scale)), radius, int(cv2.GC_FGD), -1)
        for x, y in guide.negative:
            cv2.circle(init, (int(x * scale), int(y * scale)), radius, int(cv2.GC_BGD), -1)

    def track(self, read_frame: Callable[[int], Any], frames: List[int], prompt_frame: int, prompts: Prompts,
              sink: MaskSink, checkpoint: Callable[[], None], guides: Optional[Dict[int, Prompts]] = None) -> None:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        guides = guides or {}
        prev_image = read_frame(frames[0])
        prev_mask = self.segment(prev_image, prompts)
        sink(frames[0], prev_mask)
        scale = self._scale(prev_image)
        for frame in frames[1:]:
            checkpoint()
            image = read_frame(frame)
            prev_small = cv2.cvtColor(self._resize(prev_image, scale), cv2.COLOR_RGB2GRAY)
            cur_small_rgb = self._resize(image, scale)
            cur_small = cv2.cvtColor(cur_small_rgb, cv2.COLOR_RGB2GRAY)
            mask_small = self._resize(prev_mask.astype(np.uint8), scale, nearest=True)
            # Backward flow (current -> previous) lets us pull the previous mask onto the current frame.
            flow = cv2.calcOpticalFlowFarneback(cur_small, prev_small, None, 0.5, 4, 21, 3, 5, 1.1, 0)
            h, w = cur_small.shape
            grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            warped = cv2.remap(mask_small.astype(np.float32), grid_x + flow[..., 0], grid_y + flow[..., 1],
                               cv2.INTER_LINEAR) > 0.5
            guide = guides.get(frame)
            if guide is not None and not self._agrees(warped, guide, scale):
                # Lost the subject (occlusion, fast motion): restart from the Maya guide on this frame.
                mask = self.segment(image, guide)
            elif not warped.any():
                mask = np.zeros(image.shape[:2], bool)
            else:
                band = max(3, int(min(h, w) * 0.02))
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
                warped_u8 = warped.astype(np.uint8)
                sure_fg = cv2.erode(warped_u8, kernel).astype(bool)
                maybe = cv2.dilate(warped_u8, kernel).astype(bool)
                init = np.full((h, w), cv2.GC_BGD, np.uint8)
                init[maybe] = cv2.GC_PR_BGD
                init[warped] = cv2.GC_PR_FGD
                init[sure_fg] = cv2.GC_FGD
                if guide is not None:
                    self._apply_guide(init, guide, scale, max(3, int(min(h, w) * 0.012)))
                mask_small = self._grabcut(cur_small_rgb, init, None)
                ys, xs = np.nonzero(sure_fg)
                keep = list(zip(xs[:: max(1, len(xs) // 20)].tolist(), ys[:: max(1, len(ys) // 20)].tolist()))
                if guide is not None:
                    keep += [(int(x * scale), int(y * scale)) for x, y in guide.positive]
                mask_small = self._largest_components(mask_small, keep)
                mask = cv2.resize(mask_small.astype(np.uint8), (image.shape[1], image.shape[0]),
                                  interpolation=cv2.INTER_LINEAR) > 0 if scale != 1.0 else mask_small
            sink(frame, mask)
            prev_image, prev_mask = image, mask


# ---------------------------------------------------------------------------------------- SAM 2
class Sam2Backend(SegmentationBackend):
    """SAM 2.1 via the ``sam2`` package. Loaded once; reused for images and videos."""

    name = "sam2"

    def __init__(self, checkpoint_path: Path, config_name: str, device: str = "auto") -> None:
        try:
            import torch  # type: ignore
            from sam2.build_sam import build_sam2, build_sam2_video_predictor  # type: ignore
            from sam2.sam2_image_predictor import SAM2ImagePredictor  # type: ignore
        except ImportError as exc:
            raise BackendUnavailable("SAM 2 needs 'torch' and 'sam2' in the engine environment "
                                     "(scripts/setup_engine.ps1 -WithSam2): %s" % exc)
        if not Path(checkpoint_path).is_file():
            raise BackendUnavailable("SAM 2 weights not found: %s" % checkpoint_path)
        self.torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        log.info("Loading SAM 2", extra={"checkpoint": str(checkpoint_path), "device": device})
        self._image_predictor = SAM2ImagePredictor(build_sam2(config_name, str(checkpoint_path), device=device))
        self._video_predictor = build_sam2_video_predictor(config_name, str(checkpoint_path), device=device)

    def _autocast(self) -> Any:
        import contextlib
        if self.device == "cuda":
            return self.torch.autocast("cuda", dtype=self.torch.bfloat16)
        return contextlib.nullcontext()

    @staticmethod
    def _prompt_arrays(prompts: Prompts) -> Tuple[Any, Any, Any]:
        import numpy as np  # type: ignore
        points = np.array([[x, y] for x, y, _ in prompts.points], dtype=np.float32) if prompts.points else None
        labels = np.array([label for _, _, label in prompts.points], dtype=np.int32) if prompts.points else None
        box = np.array(prompts.box, dtype=np.float32) if prompts.box is not None else None
        return points, labels, box

    def segment(self, image: Any, prompts: Prompts) -> Any:
        points, labels, box = self._prompt_arrays(prompts)
        with self.torch.inference_mode(), self._autocast():
            self._image_predictor.set_image(image)
            masks, scores, _ = self._image_predictor.predict(point_coords=points, point_labels=labels, box=box,
                                                             multimask_output=False)
        return masks[0] > 0

    def track(self, read_frame: Callable[[int], Any], frames: List[int], prompt_frame: int, prompts: Prompts,
              sink: MaskSink, checkpoint: Callable[[], None], guides: Optional[Dict[int, Prompts]] = None) -> None:
        from ai_engine.media.frame_source import write_jpeg
        guides = guides or {}
        points, labels, box = self._prompt_arrays(prompts)
        # The SAM 2 video predictor reads a folder of "<index>.jpg" frames.
        folder = Path(tempfile.mkdtemp(prefix="mait-sam2-"))
        try:
            for index, frame in enumerate(frames):
                checkpoint()
                write_jpeg(folder / ("%05d.jpg" % index), read_frame(frame), quality=95)
            with self.torch.inference_mode(), self._autocast():
                state = self._video_predictor.init_state(video_path=str(folder), offload_video_to_cpu=True)
                self._video_predictor.add_new_points_or_box(inference_state=state, frame_idx=0, obj_id=1,
                                                            points=points, labels=labels, box=box)
                # Maya-tracking guides become extra conditioning prompts on their frames (SAM 2
                # supports prompts on several frames of one object; propagation uses all of them).
                for index, frame in enumerate(frames):
                    guide = guides.get(frame)
                    if index == 0 or guide is None:
                        continue
                    g_points, g_labels, g_box = self._prompt_arrays(guide)
                    self._video_predictor.add_new_points_or_box(inference_state=state, frame_idx=index, obj_id=1,
                                                                points=g_points, labels=g_labels, box=g_box)
                for frame_idx, _obj_ids, mask_logits in self._video_predictor.propagate_in_video(state):
                    checkpoint()
                    sink(frames[frame_idx], (mask_logits[0] > 0.0).cpu().numpy()[0])
                self._video_predictor.reset_state(state)
        finally:
            shutil.rmtree(str(folder), ignore_errors=True)


# ---------------------------------------------------------------------------------------- cache
class BackendCache:
    """Creates backends on demand and keeps the most recent one loaded."""

    def __init__(self, model_manager: Any, device: str = "auto") -> None:
        self._models = model_manager
        self._device = device
        self._lock = threading.Lock()
        self._current: Optional[Tuple[str, SegmentationBackend]] = None
        #: serialises inference so two jobs never fight over GPU memory
        self.inference_lock = threading.Lock()

    def get(self, model_id: str) -> SegmentationBackend:
        with self._lock:
            if self._current and self._current[0] == model_id:
                return self._current[1]
            info = self._models.get(model_id)
            if not info.installed:
                raise BackendUnavailable("model %s is not installed" % model_id)
            if info.backend == "classical":
                backend: SegmentationBackend = ClassicalBackend()
            elif info.backend == "sam2":
                self._current = None  # free the previous model before loading another
                backend = Sam2Backend(self._models.file_path(model_id), info.options.get("config", ""), self._device)
            else:
                raise BackendUnavailable("unknown backend %s" % info.backend)
            self._current = (model_id, backend)
            return backend

    def evict(self, model_id: str) -> None:
        """Unload ``model_id`` if it is the loaded model (before re-install / removal)."""
        with self._lock:
            if self._current and self._current[0] == model_id:
                self._current = None

    def loaded(self) -> Optional[str]:
        current = self._current
        return current[0] if current else None

    def capabilities(self) -> Dict[str, bool]:
        caps = {}
        for module in ("cv2", "numpy", "torch", "sam2"):
            try:
                __import__(module)
                caps[module] = True
            except ImportError:
                caps[module] = False
        return caps
