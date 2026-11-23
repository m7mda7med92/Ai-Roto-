"""Matte refinement: shrink/grow, feather, and conversion to 8-bit alpha."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from shared.contracts.errors import ContractError


@dataclass
class RefineSettings:
    grow_px: int = 0        # negative = shrink
    feather_px: float = 0.0
    fill_holes: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "RefineSettings":
        data = data or {}
        if not isinstance(data, dict):
            raise ContractError("refine must be an object", "refine")
        grow = data.get("grow_px", 0)
        feather = data.get("feather_px", 0.0)
        if isinstance(grow, bool) or not isinstance(grow, int) or not -200 <= grow <= 200:
            raise ContractError("grow_px must be an integer in [-200, 200]", "refine.grow_px")
        if isinstance(feather, bool) or not isinstance(feather, (int, float)) or not 0 <= feather <= 200:
            raise ContractError("feather_px must be in [0, 200]", "refine.feather_px")
        return cls(grow_px=grow, feather_px=float(feather), fill_holes=bool(data.get("fill_holes", False)))

    def to_dict(self) -> Dict[str, Any]:
        return {"grow_px": self.grow_px, "feather_px": self.feather_px, "fill_holes": self.fill_holes}


def refine_mask(mask: Any, settings: RefineSettings) -> Any:
    """Boolean/0-1 mask -> uint8 alpha (0..255) with optional fill, grow/shrink and feather."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    alpha = (np.asarray(mask) > 0).astype(np.uint8) * 255
    if settings.fill_holes and alpha.any():
        contours, _ = cv2.findContours(alpha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        alpha = np.zeros_like(alpha)
        cv2.drawContours(alpha, contours, -1, 255, thickness=cv2.FILLED)
    if settings.grow_px:
        radius = abs(settings.grow_px)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
        alpha = cv2.dilate(alpha, kernel) if settings.grow_px > 0 else cv2.erode(alpha, kernel)
    if settings.feather_px > 0:
        sigma = settings.feather_px / 2.0
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return alpha
