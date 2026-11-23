"""Depth & Geometry panel: depth map, point cloud, meshing, ground plane, reconstruction."""
from __future__ import annotations

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, check, combo, future_button, grid, spin
from maya_plugin.ui.qt import QtWidgets


class DepthGeometryPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        depth = Card("Depth Map Preview", badge="Phase 04")
        depth.add(ViewerPlaceholder("Depth map", 200))
        cloud = Card("Point Cloud Preview", badge="Phase 04")
        cloud.add(ViewerPlaceholder("Point cloud", 200))
        layout.addLayout(grid([depth, cloud]))
        mesh = Card("Mesh Generation")
        mesh.add_form([("Method", combo(["Poisson", "TSDF fusion", "Ball pivoting"])), ("Detail", spin(1, 12, 8)),
                       ("Decimate to", spin(1000, 5000000, 200000, suffix=" faces"))])
        mesh.add(future_button("Generate Mesh", "Phase 04"))
        ground = Card("Ground Plane")
        ground.add_form([("Detection", combo(["Auto (from depth)", "From selected tracks"])),
                         ("Align scene up-axis", check("Align to Y-up", True))])
        ground.add(future_button("Detect Ground Plane", "Phase 04"))
        recon = Card("Geometry Reconstruction")
        recon.add_form([("Source", combo(["Depth + solved camera", "Multi-view"])),
                        ("Scale reference", QtWidgets.QLineEdit("1.0")), ("Textures", check("Project textures"))])
        recon.add(future_button("Reconstruct", "Phase 04"))
        depth_settings = Card("Depth Settings")
        depth_settings.add_form([("Model", combo(["(installed in Phase 02)"])), ("Output", combo(["Relative", "Metric"])),
                                 ("Temporal consistency", check("Stabilise over time", True))])
        layout.addLayout(grid([mesh, ground, recon, depth_settings]))
