"""Guide prompts from the artist's own Maya tracking (Phase 03).

The artist tracks the shot in Maya (camera solve, object track, locators or tracked
geometry). Roto Studio reads that data from the scene (query-only, see
``maya_bridge.read_guide_scene``), and this module projects it onto the plate:

* *include* locators  -> include points
* *exclude* locators  -> exclude points
* *include* geometry  -> a box around its projected (sampled) vertices
* *exclude* geometry  -> an exclude point at its projected centre

The per-frame prompts are sent with the ``roto_track`` job, where the AI tracker uses them to
stay locked on the subject. Pure Python (no Qt, no Maya, no numpy) so it is unit-testable.

Projection follows Maya's camera model: focal length and film aperture (inches), lens squeeze,
film offset and the film fit (fill / horizontal / vertical / overscan) against the plate aspect.
Not modelled: pre/post scale, film roll, camera shake/pan-zoom, lens distortion, deforming
(skinned) meshes (only the transform's animation moves the sampled vertices).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

MM_PER_INCH = 25.4
FIT_FILL, FIT_HORIZONTAL, FIT_VERTICAL, FIT_OVERSCAN = 0, 1, 2, 3
INCLUDE, EXCLUDE = "include", "exclude"
Matrix = List[float]  # Maya 4x4, row-major, row-vector convention (translation in 12, 13, 14)


@dataclass
class CameraSample:
    world_matrix: Matrix
    focal_mm: float
    h_aperture_in: float
    v_aperture_in: float
    film_fit: int = FIT_FILL
    h_offset_in: float = 0.0
    v_offset_in: float = 0.0
    lens_squeeze: float = 1.0


@dataclass
class GuideObject:
    name: str
    role: str                                         # INCLUDE / EXCLUDE
    kind: str                                         # "point" (locator/transform) or "mesh"
    local_points: List[Tuple[float, float, float]]    # object space
    world_matrices: Dict[int, Matrix] = field(default_factory=dict)  # Maya frame -> matrix


@dataclass
class GuideScene:
    camera: str
    cameras: Dict[int, CameraSample]                  # Maya frame -> sample
    objects: List[GuideObject]


# ------------------------------------------------------------------------------------------ math
def mat_inverse(m: Sequence[float]) -> Matrix:
    """General 4x4 inverse (Gauss-Jordan); raises ValueError when singular."""
    a = [list(m[r * 4:(r + 1) * 4]) + [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("matrix is singular")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(4):
            if r != col and a[r][col] != 0.0:
                factor = a[r][col]
                a[r] = [v - factor * w for v, w in zip(a[r], a[col])]
    return [a[r][4 + c] for r in range(4) for c in range(4)]


def transform_point(p: Sequence[float], m: Sequence[float]) -> Tuple[float, float, float]:
    """Row vector times matrix (Maya convention)."""
    x, y, z = p
    w = x * m[3] + y * m[7] + z * m[11] + m[15]
    w = w if abs(w) > 1e-12 else 1.0
    return ((x * m[0] + y * m[4] + z * m[8] + m[12]) / w,
            (x * m[1] + y * m[5] + z * m[9] + m[13]) / w,
            (x * m[2] + y * m[6] + z * m[10] + m[14]) / w)


def view_size_mm(cam: CameraSample, width: int, height: int, pixel_aspect: float = 1.0) -> Tuple[float, float]:
    """Width/height (mm) of the film area that maps onto the plate, after the film fit."""
    film_w = cam.h_aperture_in * cam.lens_squeeze * MM_PER_INCH
    film_h = cam.v_aperture_in * MM_PER_INCH
    image_aspect = width * pixel_aspect / float(height)
    fit = cam.film_fit
    if fit == FIT_FILL:
        fit = FIT_VERTICAL if film_w / film_h > image_aspect else FIT_HORIZONTAL
    elif fit == FIT_OVERSCAN:
        fit = FIT_HORIZONTAL if film_w / film_h > image_aspect else FIT_VERTICAL
    if fit == FIT_VERTICAL:
        return film_h * image_aspect, film_h
    return film_w, film_w / image_aspect


def project(world_point: Sequence[float], cam: CameraSample, cam_inverse: Sequence[float], width: int,
            height: int, pixel_aspect: float = 1.0) -> Optional[Tuple[float, float]]:
    """World point -> plate pixel (x right, y down); ``None`` if behind the camera."""
    x, y, z = transform_point(world_point, cam_inverse)
    if z > -1e-6:  # Maya cameras look down -Z
        return None
    x_mm = cam.focal_mm * x / -z - cam.h_offset_in * MM_PER_INCH
    y_mm = cam.focal_mm * y / -z - cam.v_offset_in * MM_PER_INCH
    view_w, view_h = view_size_mm(cam, width, height, pixel_aspect)
    return (x_mm / view_w + 0.5) * width, (0.5 - y_mm / view_h) * height


# ------------------------------------------------------------------------------------------ guides
def _inside(u: float, v: float, width: int, height: int) -> bool:
    return 0 <= u < width and 0 <= v < height


def build_guides(scene: GuideScene, plate_frames: Sequence[int], width: int, height: int,
                 frame_offset: int = 0, pixel_aspect: float = 1.0) -> Dict[int, Dict[str, Any]]:
    """Per plate frame prompts ``{"points": [[x, y, label]], "box": [x0, y0, x1, y1] | None}``.

    Maya frame = plate frame + ``frame_offset``. Frames where nothing usable is on screen are left out.
    """
    guides: Dict[int, Dict[str, Any]] = {}
    for frame in plate_frames:
        cam = scene.cameras.get(frame + frame_offset)
        if cam is None:
            continue
        try:
            inverse = mat_inverse(cam.world_matrix)
        except ValueError:
            continue
        points: List[List[float]] = []
        box: Optional[List[float]] = None
        for obj in scene.objects:
            matrix = obj.world_matrices.get(frame + frame_offset)
            if matrix is None:
                continue
            projected = [project(transform_point(p, matrix), cam, inverse, width, height, pixel_aspect)
                         for p in obj.local_points]
            projected = [uv for uv in projected if uv is not None]
            if not projected:
                continue
            label = 1 if obj.role == INCLUDE else 0
            if obj.kind == "mesh" and obj.role == INCLUDE:
                us = [u for u, _ in projected]
                vs = [v for _, v in projected]
                x0, y0 = max(0.0, min(us)), max(0.0, min(vs))
                x1, y1 = min(width - 1.0, max(us)), min(height - 1.0, max(vs))
                if x1 - x0 < 2 or y1 - y0 < 2:
                    continue
                box = [x0, y0, x1, y1] if box is None else [min(box[0], x0), min(box[1], y0),
                                                            max(box[2], x1), max(box[3], y1)]
            elif obj.kind == "mesh":
                u = sum(uv[0] for uv in projected) / len(projected)
                v = sum(uv[1] for uv in projected) / len(projected)
                if _inside(u, v, width, height):
                    points.append([u, v, label])
            else:
                for u, v in projected:
                    if _inside(u, v, width, height):
                        points.append([u, v, label])
        if box is None and not any(p[2] == 1 for p in points):
            continue  # nothing of the subject on screen on this frame
        guides[frame] = {"points": [[round(x, 2), round(y, 2), lab] for x, y, lab in points],
                         "box": [round(v, 2) for v in box] if box else None}
    return guides


def guides_payload(guides: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """JSON-ready form for the ``roto_track`` request (frame keys as strings)."""
    return {str(frame): value for frame, value in sorted(guides.items())}


def sample_points(points: Sequence[Sequence[float]], limit: int = 400) -> List[Tuple[float, float, float]]:
    """Evenly thin a vertex list to at most ~``limit`` points (the projected box stays tight)."""
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in points]
    step = max(1, -(-len(pts) // max(1, limit)))
    return pts[::step]
