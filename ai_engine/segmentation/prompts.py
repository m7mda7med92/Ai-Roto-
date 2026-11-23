"""User prompts for segmentation: positive/negative points and an optional box, in source pixels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from shared.contracts.errors import ContractError


@dataclass
class Prompts:
    points: List[Tuple[float, float, int]] = field(default_factory=list)   # (x, y, 1=include / 0=exclude)
    box: Optional[Tuple[float, float, float, float]] = None               # (x0, y0, x1, y1)

    @property
    def positive(self) -> List[Tuple[float, float]]:
        return [(x, y) for x, y, label in self.points if label == 1]

    @property
    def negative(self) -> List[Tuple[float, float]]:
        return [(x, y) for x, y, label in self.points if label == 0]

    def to_dict(self) -> Dict[str, Any]:
        return {"points": [list(p) for p in self.points], "box": list(self.box) if self.box else None}

    @classmethod
    def from_dict(cls, data: Any, width: int = 0, height: int = 0) -> "Prompts":
        if not isinstance(data, dict):
            raise ContractError("prompts must be an object", "prompts")
        points = []
        for item in data.get("points") or []:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ContractError("each point must be [x, y, label]", "prompts.points")
            x, y, label = item
            if label not in (0, 1) or isinstance(label, bool):
                raise ContractError("point label must be 1 (include) or 0 (exclude)", "prompts.points")
            points.append((float(x), float(y), int(label)))
        box = data.get("box")
        if box is not None:
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise ContractError("box must be [x0, y0, x1, y1]", "prompts.box")
            x0, y0, x1, y1 = (float(v) for v in box)
            box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                raise ContractError("box is too small", "prompts.box")
        prompts = cls(points=points, box=box)
        if not prompts.positive and prompts.box is None:
            raise ContractError("add at least one include point or a box", "prompts")
        if width and height:
            for x, y, _ in prompts.points:
                if not (0 <= x < width and 0 <= y < height):
                    raise ContractError("point (%g, %g) is outside the %dx%d frame" % (x, y, width, height),
                                        "prompts.points")
        return prompts


def _in_frame(x: float, y: float, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height


def clip_to_frame(prompts: Prompts, width: int, height: int) -> Optional[Prompts]:
    """Guide prompts projected from Maya can fall off-frame: drop those points, clip the box.

    Returns ``None`` when nothing usable is left (e.g. the object is fully off screen).
    """
    points = [(x, y, label) for x, y, label in prompts.points if _in_frame(x, y, width, height)]
    box = None
    if prompts.box is not None:
        x0, y0, x1, y1 = prompts.box
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(width - 1.0, x1), min(height - 1.0, y1)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            box = (x0, y0, x1, y1)
    clipped = Prompts(points=points, box=box)
    if not clipped.positive and clipped.box is None:
        return None
    return clipped


#: Upper bound on guided frames in one request (keeps the request under the 1 MB body cap).
MAX_GUIDE_FRAMES = 5000


def guides_from_dict(data: Any) -> Dict[int, Prompts]:
    """Per-frame guide prompts ``{"1001": {"points": [...], "box": [...]}, ...}`` (frame keys as strings).

    Guides come from the artist's own tracking in Maya (projected locators / geometry). They are
    added to the AI tracker as extra prompts on those frames, which keeps the mask locked on.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ContractError("guides must be an object mapping frame -> prompts", "guides")
    if len(data) > MAX_GUIDE_FRAMES:
        raise ContractError("at most %d guided frames per request" % MAX_GUIDE_FRAMES, "guides")
    guides: Dict[int, Prompts] = {}
    for key, value in data.items():
        try:
            frame = int(key)
        except (TypeError, ValueError):
            raise ContractError("guide frame keys must be integers, got %r" % (key,), "guides")
        try:
            guides[frame] = Prompts.from_dict(value)
        except ContractError as exc:
            raise ContractError("guide for frame %d: %s" % (frame, exc), "guides.%d" % frame)
    return guides


def guides_to_dict(guides: Dict[int, Prompts]) -> Dict[str, Any]:
    return {str(frame): prompts.to_dict() for frame, prompts in sorted(guides.items())}
