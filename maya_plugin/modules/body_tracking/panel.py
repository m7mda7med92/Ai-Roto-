"""Body Tracking panel: detection, multi-person, skeleton preview, mocap and retargeting."""
from __future__ import annotations

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, check, combo, future_button, grid, spin
from maya_plugin.ui.qt import QtWidgets


class BodyTrackingPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        detection = Card("Person Detection", badge="Phase 05")
        detection.add_form([("Detector", combo(["Auto", "Fast", "Accurate"])),
                            ("Min confidence", spin(0.0, 1.0, 0.5, 2, step=0.05))])
        detection.add(future_button("Detect People", "Phase 05"))
        multi = Card("Multi-Person Tracking")
        multi.add_form([("Max people", spin(1, 20, 2)), ("Track IDs", check("Keep person IDs stable", True))])
        layout.addLayout(grid([detection, multi]))
        skeleton = Card("Skeleton Preview", badge="Phase 05")
        skeleton.add(ViewerPlaceholder("2D / 3D skeleton overlay", 220))
        layout.addWidget(skeleton)
        mocap = Card("Motion Capture Settings")
        mocap.add_form([("Skeleton format", combo(["SMPL-X", "HumanIK", "Custom"])),
                        ("Hands", check("Track hands")), ("Face", check("Track face")),
                        ("Smoothing", spin(0.0, 1.0, 0.3, 2, step=0.05)),
                        ("Foot contact", check("Lock feet on contact", True))])
        retarget = Card("Retargeting Settings")
        retarget.add_form([("Target rig", combo(["Select in Maya (future)", "HumanIK character"])),
                           ("Scale", combo(["Auto (height match)", "Manual"])),
                           ("Bake", check("Bake to keys on apply", True))])
        retarget.add(future_button("Retarget to Rig", "Phase 06"))
        layout.addLayout(grid([mocap, retarget]))
