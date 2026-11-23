"""Maya camera projection with numpy (same model as ``maya_plugin/modules/roto_studio/maya_guides.py``).

Camera samples come from the artist's tracked Maya camera, read in Maya (query only) and written to
the fit input file. Matrices are Maya's row-major, row-vector 4x4 (translation in elements 12-14).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

MM_PER_INCH = 25.4
FIT_FILL, FIT_HORIZONTAL, FIT_VERTICAL, FIT_OVERSCAN = 0, 1, 2, 3


@dataclass
class Camera:
    world: Any          # 4x4 numpy, row-vector convention
    inverse: Any        # 4x4 numpy
    focal_mm: float
    view_w_mm: float
    view_h_mm: float
    h_offset_mm: float
    v_offset_mm: float
    width: int
    height: int

    @property
    def position(self) -> Any:
        return self.world[3, :3]

    @property
    def rotation(self) -> Any:
        """3x3 camera -> world rotation for row vectors (rows = camera axes in world, unit length)."""
        import numpy as np  # type: ignore
        rot = np.array(self.world[:3, :3], dtype=float)
        return rot / np.linalg.norm(rot, axis=1, keepdims=True)

    @property
    def focal_px(self) -> float:
        return self.focal_mm / self.view_w_mm * self.width

    def project(self, points: Any) -> Tuple[Any, Any]:
        """World points (..., 3) -> pixels (..., 2) and camera depth -z (..., positive in front)."""
        import numpy as np  # type: ignore
        cam = points @ self.inverse[:3, :3] + self.inverse[3, :3]
        depth = -cam[..., 2]
        safe = np.maximum(depth, 1e-6)
        x_mm = self.focal_mm * cam[..., 0] / safe - self.h_offset_mm
        y_mm = self.focal_mm * cam[..., 1] / safe - self.v_offset_mm
        u = (x_mm / self.view_w_mm + 0.5) * self.width
        v = (0.5 - y_mm / self.view_h_mm) * self.height
        return np.stack([u, v], axis=-1), depth

    def ray(self, u: float, v: float) -> Any:
        """World direction through pixel (u, v), scaled so one unit along it is one unit of depth."""
        import numpy as np  # type: ignore
        x_mm = (u / self.width - 0.5) * self.view_w_mm + self.h_offset_mm
        y_mm = (0.5 - v / self.height) * self.view_h_mm + self.v_offset_mm
        direction_cam = np.array([x_mm / self.focal_mm, y_mm / self.focal_mm, -1.0])
        return direction_cam @ self.world[:3, :3] / np.linalg.norm(self.world[2, :3])


def view_size_mm(h_aperture_in: float, v_aperture_in: float, lens_squeeze: float, film_fit: int,
                 width: int, height: int, pixel_aspect: float = 1.0) -> Tuple[float, float]:
    film_w = h_aperture_in * lens_squeeze * MM_PER_INCH
    film_h = v_aperture_in * MM_PER_INCH
    image_aspect = width * pixel_aspect / float(height)
    fit = film_fit
    if fit == FIT_FILL:
        fit = FIT_VERTICAL if film_w / film_h > image_aspect else FIT_HORIZONTAL
    elif fit == FIT_OVERSCAN:
        fit = FIT_HORIZONTAL if film_w / film_h > image_aspect else FIT_VERTICAL
    if fit == FIT_VERTICAL:
        return film_h * image_aspect, film_h
    return film_w, film_w / image_aspect


def camera_from_dict(data: Dict[str, Any], width: int, height: int) -> Camera:
    """A camera sample as written by Maya (``maya_guides.CameraSample`` fields)."""
    import numpy as np  # type: ignore
    world = np.array([float(v) for v in data["world_matrix"]], dtype=float).reshape(4, 4)
    view_w, view_h = view_size_mm(float(data["h_aperture_in"]), float(data["v_aperture_in"]),
                                  float(data.get("lens_squeeze", 1.0)), int(data.get("film_fit", 0)), width, height)
    return Camera(world=world, inverse=np.linalg.inv(world), focal_mm=float(data["focal_mm"]),
                  view_w_mm=view_w, view_h_mm=view_h,
                  h_offset_mm=float(data.get("h_offset_in", 0.0)) * MM_PER_INCH,
                  v_offset_mm=float(data.get("v_offset_in", 0.0)) * MM_PER_INCH, width=width, height=height)
