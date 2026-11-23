"""Camera Tracking panel: footage, camera/lens/sensor, tracking and solve settings."""
from __future__ import annotations

from typing import List, Tuple

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, check, combo, future_button, grid, spin
from maya_plugin.ui.qt import QtWidgets

SENSOR_PRESETS = {"Custom": (36.0, 24.0), "Full Frame 35mm": (36.0, 24.0), "Super 35": (24.89, 18.66),
                  "ARRI Alexa Mini LF (Open Gate)": (36.70, 25.54), "RED Komodo 6K": (27.03, 14.26),
                  "Micro Four Thirds": (17.3, 13.0)}


class CameraTrackingPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        viewer = Card("Footage Viewer", badge="Phase 03")
        viewer.add(ViewerPlaceholder("Footage viewer\n2D tracks and solve overlay will display here", 260))
        layout.addWidget(viewer)

        camera = Card("Camera Settings")
        camera.add_form([("Camera model", combo(["Pinhole", "Pinhole + distortion"])),
                         ("Frame rate", combo(["23.976", "24", "25", "29.97", "30"], 1)),
                         ("Pixel aspect", spin(0.5, 2.0, 1.0, 3, step=0.01))])
        lens = Card("Lens Settings")
        lens.add_form([("Distortion model", combo(["None", "Radial (k1,k2)", "Brown-Conrady", "3DE Classic"])),
                       ("Anamorphic squeeze", spin(1.0, 2.0, 1.0, 2, step=0.05)),
                       ("Lens grid", future_button("Load lens grid...", "Phase 04"))])
        sensor = Card("Sensor Settings")
        self.sensor_preset = combo(list(SENSOR_PRESETS))
        self.sensor_w = spin(1.0, 80.0, 36.0, 2, " mm", 0.1)
        self.sensor_h = spin(1.0, 80.0, 24.0, 2, " mm", 0.1)
        self.sensor_preset.currentTextChanged.connect(self._apply_preset)
        sensor.add_form([("Preset", self.sensor_preset), ("Width", self.sensor_w), ("Height", self.sensor_h)])
        focal = Card("Focal Length")
        self.focal_known = check("Known focal length", True)
        self.focal = spin(4.0, 1200.0, 35.0, 2, " mm", 0.5)
        focal.add_form([("", self.focal_known), ("Focal length", self.focal),
                        ("Zoom", combo(["Fixed (prime)", "Varying (zoom)"]))])
        layout.addLayout(grid([camera, lens, sensor, focal]))

        tracking = Card("Tracking Settings", badge="Phase 03")
        tracking.add_form([("Feature detector", combo(["Auto", "Corners", "Learned features"])),
                           ("Max features", spin(50, 5000, 400)), ("Pattern size", spin(5, 101, 21, suffix=" px")),
                           ("Search size", spin(10, 401, 61, suffix=" px"))])
        tracking.add(future_button("Track Forward", "Phase 03"))
        solve = Card("Solve Settings", badge="Phase 03")
        solve.add_form([("Solve mode", combo(["Free camera", "Tripod (nodal)"])),
                        ("Refine focal", check("Refine focal length")), ("Refine distortion", check("Refine distortion")),
                        ("Target error", spin(0.05, 5.0, 0.5, 2, " px", 0.05))])
        solve.add(future_button("Solve Camera", "Phase 03"))
        layout.addLayout(grid([tracking, solve]))

        graph = Card("Track Error Graph", badge="Phase 03")
        graph.add(ViewerPlaceholder("Per-frame reprojection error graph", 140))
        layout.addWidget(graph)

    def _apply_preset(self, name: str) -> None:
        width, height = SENSOR_PRESETS.get(name, (36.0, 24.0))
        self.sensor_w.setValue(width)
        self.sensor_h.setValue(height)

    def parameter_widgets(self) -> List[Tuple[str, QtWidgets.QWidget]]:
        return [("Start frame", spin(-100000, 100000, 1001)), ("End frame", spin(-100000, 100000, 1100))]
