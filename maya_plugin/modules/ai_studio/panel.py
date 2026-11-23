"""AI Studio panel. Job submission is live (simulated job); model inference arrives in Phase 02."""
from __future__ import annotations

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.ui.components.widgets import Card, ViewerPlaceholder, accent_button, combo, grid, path_field, spin
from maya_plugin.ui.qt import QtWidgets
from shared.constants.engine_constants import ConnectionState


class AIStudioPanel(BaseModulePanel):
    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        inputs = Card("Inputs")
        self.image_input = path_field("Image or image sequence", "Image input",
                                      file_filter="Images (*.exr *.png *.jpg *.jpeg *.tif *.tiff *.dpx)")
        self.video_input = path_field("Video file", "Video input", file_filter="Video (*.mov *.mp4 *.avi *.mxf)")
        inputs.add_form([("Image input", self.image_input), ("Video input", self.video_input)])
        preview = Card("Input Preview", badge="Phase 02")
        preview.add(ViewerPlaceholder("Input preview\n(media decoding arrives with the video pipeline)", 160))
        layout.addLayout(grid([inputs, preview]))

        model = Card("Model Selection", badge="Phase 02")
        self.model_combo = combo(["No models installed"])
        self.model_combo.setEnabled(False)
        model.add_form([("Model", self.model_combo), ("Task", combo(["Segmentation", "Depth", "Pose", "Tracking"]))])
        processing = Card("Processing Settings")
        processing.add_form([("Device", combo(["Auto", "CUDA (GPU)", "CPU"])),
                             ("Frame range", QtWidgets.QLineEdit("1001-1100")),
                             ("Resolution scale", combo(["100%", "50%", "25%"])),
                             ("Tile size", spin(128, 4096, 1024, suffix=" px"))])
        layout.addLayout(grid([model, processing]))

        gpu = Card("GPU Information (reported by engine)")
        self.gpu_text = QtWidgets.QLabel("Engine not connected.")
        self.gpu_text.setWordWrap(True)
        gpu.add(self.gpu_text)
        submit = Card("AI Job Submission")
        note = QtWidgets.QLabel("Phase 01 submits a <b>simulated</b> job to verify the engine pipeline. "
                                "No AI model runs and nothing in the Maya scene changes.")
        note.setWordWrap(True)
        self.submit_button = accent_button("Submit Test Job")
        self.submit_button.clicked.connect(lambda: self.app.jobs.submit_test_job())
        submit.add(note)
        submit.add(self.submit_button)
        layout.addLayout(grid([gpu, submit]))

        self.bridge.engine_health.connect(self.on_health)
        self.bridge.connection_changed.connect(self.on_connection)
        self.on_connection(self.app.connection.status)

    def on_connection(self, status: object) -> None:
        connected = getattr(status, "state", None) == ConnectionState.CONNECTED
        self.submit_button.setEnabled(connected)
        health = getattr(status, "health", None)
        if connected and health is not None:
            self.on_health(health)
        elif not connected:
            self.gpu_text.setText("Engine not connected.")

    def on_health(self, health: object) -> None:
        devices = getattr(health, "gpu_devices", [])
        lines = ["Engine Python %s on %s" % (getattr(health, "python_version", "?"), getattr(health, "platform", "?")),
                 "CUDA capable: %s" % ("yes" if getattr(health, "cuda_available", False) else "no"),
                 "Detection: %s" % getattr(health, "gpu_detection_method", "none")]
        lines += ["GPU %d: %s, %s MB, driver %s" % (d.index, d.name, d.memory_total_mb or "?", d.driver_version or "?")
                  for d in devices] or ["No NVIDIA GPU detected"]
        self.gpu_text.setText("\n".join(lines))
