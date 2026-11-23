"""Roto Studio panel: AI/manual masking, subject selection, mask tracking, matte refinement."""
from __future__ import annotations

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, check, combo, future_button, grid, spin
from maya_plugin.ui.qt import QtWidgets


class RotoStudioPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        viewer = Card("Roto Viewer", badge="Phase 02")
        viewer.add(ViewerPlaceholder("Frame viewer with mask overlay", 260))
        layout.addWidget(viewer)
        ai = Card("AI Masking", badge="Phase 02")
        ai.add_form([("Model", combo(["(installed in Phase 02)"])), ("Prompt type", combo(["Points", "Box", "Text"])),
                     ("Output", combo(["Alpha matte", "Shapes (splines)", "Both"]))])
        ai.add(future_button("Generate Mask", "Phase 02"))
        manual = Card("Manual Masking")
        manual.add_form([("Shape", combo(["Bezier", "B-Spline", "Freehand"])), ("Feather", spin(0, 200, 4, suffix=" px")),
                         ("Motion blur", check("Shape motion blur"))])
        subject = Card("Subject Selection")
        subject.add_form([("Subjects", combo(["Single subject", "Multiple subjects"])),
                          ("Selection", combo(["Click points", "Draw box"]))])
        tracking = Card("Mask Tracking", badge="Phase 02")
        tracking.add_form([("Direction", combo(["Forward", "Backward", "Both"])),
                           ("Keyframe interval", spin(1, 100, 10))])
        tracking.add(future_button("Track Mask", "Phase 02"))
        layout.addLayout(grid([ai, manual, subject, tracking]))
        refine = Card("Matte Refinement")
        refine.add_form([("Edge refinement", combo(["Off", "Fine (hair)", "Hard surface"])),
                         ("Shrink / grow", spin(-50, 50, 0, suffix=" px")), ("Despill", check("Despill edges")),
                         ("Temporal smoothing", spin(0.0, 1.0, 0.2, 2, step=0.05))])
        layout.addWidget(refine)
