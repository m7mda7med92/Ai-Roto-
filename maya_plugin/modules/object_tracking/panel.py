"""Object Tracking panel: selection, 2D/3D/planar/multi-object tracking, occlusion."""
from __future__ import annotations

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, check, combo, future_button, grid, spin
from maya_plugin.ui.qt import QtWidgets


class ObjectTrackingPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        selection = Card("Object Selection", badge="Phase 03")
        selection.add(ViewerPlaceholder("Draw a box or click an object to select it", 200))
        selection.add_form([("Selection mode", combo(["Bounding box", "Click (AI segment)", "Existing mask"]))])
        layout.addWidget(selection)
        t2d = Card("2D Tracking")
        t2d.add_form([("Tracker", combo(["Point", "Region", "AI (learned)"])), ("Pattern", spin(5, 201, 31, suffix=" px")),
                      ("Subpixel", check("Subpixel refinement", True))])
        t2d.add(future_button("Track 2D", "Phase 03"))
        t3d = Card("3D Tracking")
        t3d.add_form([("Rigid object", check("Assume rigid body", True)),
                      ("Requires camera", combo(["Solved camera", "Static camera"]))])
        t3d.add(future_button("Solve Object", "Phase 04"))
        planar = Card("Planar Tracking")
        planar.add_form([("Motion model", combo(["Perspective", "Affine", "Similarity", "Translation"])),
                         ("Corner pin output", check("Export corner pin", True))])
        planar.add(future_button("Track Plane", "Phase 03"))
        multi = Card("Multiple Object Tracking")
        multi.add_form([("Max objects", spin(1, 100, 5)), ("Re-identification", check("Keep identities across cuts", True))])
        multi.add(future_button("Track All", "Phase 04"))
        layout.addLayout(grid([t2d, t3d, planar, multi]))
        occlusion = Card("Occlusion Settings")
        occlusion.add_form([("Occlusion handling", combo(["Predict through occlusion", "Stop at occlusion", "Use mask"])),
                            ("Max occluded frames", spin(0, 500, 12)),
                            ("Confidence threshold", spin(0.0, 1.0, 0.6, 2, step=0.05))])
        layout.addWidget(occlusion)
