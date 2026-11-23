"""Roto Studio panel: AI masking from click/box prompts, mask tracking and matte refinement.

Workflow: Load Plate (engine writes JPEG proxies) -> place prompts in the viewer ->
Segment Frame (preview matte) -> Track Mask (matte sequence written to the project's
roto folder). All heavy work runs as engine jobs; this panel only submits requests,
reads the resulting image files for display and never modifies the Maya scene.
"""
from __future__ import annotations

import os
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Optional

from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.core import maya_bridge
from maya_plugin.modules.roto_studio import maya_guides as mg
from maya_plugin.modules.roto_studio import roto_requests as rr
from maya_plugin.modules.roto_studio.viewer import RotoViewer
from maya_plugin.ui.components.widgets import Card, accent_button, check, combo, future_button, grid, path_field, spin
from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets, Signal, is_valid
from shared.constants.engine_constants import ConnectionState, JobState

PLATE_FILTER = ("Plates (*.exr *.png *.jpg *.jpeg *.tif *.tiff *.dpx *.mov *.mp4 *.avi *.mxf *.mkv *.m4v);;"
                "All files (*.*)")
DIRECTIONS = [("Both", "both"), ("Forward", "forward"), ("Backward", "backward")]


class _FutureRelay(QtCore.QObject):
    """Delivers ``Future`` results (resolved on worker threads) to the main thread."""

    done = Signal(object, object)  # (kind, JobStatus | None)

    def watch(self, future: Future, kind: str) -> None:
        def callback(f: Future) -> None:
            if is_valid(self):
                try:
                    result = f.result()
                except Exception:  # job manager already reported the error
                    result = None
                self.done.emit(kind, result)
        future.add_done_callback(callback)


class RotoStudioPanel(BaseModulePanel):
    phase_label = "Phase 03 - AI Roto + Maya guides"
    subtitle = ("Click or box a subject, preview the AI mask, then track it through the shot. "
                "Optionally guide it with your own Maya tracking.")

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        self.plate: Optional[Dict[str, Any]] = None      # media_proxies result
        self.prompts = rr.PromptState()
        self.preview: Dict[int, str] = {}                 # frame -> preview matte path
        self.track_output: Optional[Dict[str, Any]] = None
        self._pending: Dict[str, str] = {}                # job_id -> kind
        self._busy: Dict[str, Optional[str]] = {"proxies": None, "segment": None, "track": None}
        self._segment_dirty = False
        self.guides: Dict[int, Dict[str, Any]] = {}       # plate frame -> projected Maya guide
        self.guide_source = ""
        self._relay = _FutureRelay(self)
        self._relay.done.connect(self._on_submitted)
        self._auto_timer = QtCore.QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(250)
        self._auto_timer.timeout.connect(self._auto_segment)

        # ---------------------------------------------------------------- plate
        plate = Card("Plate")
        self.source_field = path_field("Image sequence (shot.####.exr) or video file", "Select plate",
                                       file_filter=PLATE_FILTER)
        self.first_frame = spin(-100000, 1000000, 1001)
        self.first_frame.setToolTip("Frame number of the first video frame (image sequences use their own numbers)")
        self.load_button = accent_button("Load Plate", "Probe the plate and build viewer proxies in the engine")
        self.load_button.clicked.connect(self.load_plate)
        self.plate_info = QtWidgets.QLabel("No plate loaded.")
        self.plate_info.setObjectName("Dim")
        self.plate_info.setWordWrap(True)
        plate.add_form([("Source", self.source_field), ("Video start frame", self.first_frame)])
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.load_button)
        row.addWidget(self.plate_info, 1)
        plate.add_layout(row)
        layout.addWidget(plate)

        # ---------------------------------------------------------------- viewer
        viewer_card = Card("Roto Viewer")
        tools = QtWidgets.QHBoxLayout()
        self.points_mode = QtWidgets.QRadioButton("Points")
        self.points_mode.setToolTip("Left-click: include. Right-click or Shift+click: exclude. Ctrl+drag: box.")
        self.box_mode = QtWidgets.QRadioButton("Box")
        self.points_mode.setChecked(True)
        self.points_mode.toggled.connect(lambda on: self.viewer.set_mode("points" if on else "box"))
        self.undo_button = QtWidgets.QPushButton("Undo Prompt")
        self.undo_button.clicked.connect(self.undo_prompt)
        self.clear_button = QtWidgets.QPushButton("Clear Prompts")
        self.clear_button.clicked.connect(self.clear_prompts)
        self.show_mask = check("Show mask", True)
        self.show_mask.toggled.connect(self._toggle_mask)
        for widget in (self.points_mode, self.box_mode, self.undo_button, self.clear_button):
            tools.addWidget(widget)
        tools.addStretch(1)
        tools.addWidget(self.show_mask)
        viewer_card.add_layout(tools)
        self.viewer = RotoViewer()
        self.viewer.setMinimumHeight(340)
        self.viewer.point_added.connect(self._on_point)
        self.viewer.box_drawn.connect(self._on_box)
        viewer_card.add(self.viewer)
        frame_row = QtWidgets.QHBoxLayout()
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setKeyboardTracking(False)
        self.frame_slider.valueChanged.connect(self.frame_spin.setValue)
        self.frame_spin.valueChanged.connect(self.frame_slider.setValue)
        self.frame_spin.valueChanged.connect(self.show_frame)
        frame_row.addWidget(QtWidgets.QLabel("Frame"))
        frame_row.addWidget(self.frame_slider, 1)
        frame_row.addWidget(self.frame_spin)
        viewer_card.add_layout(frame_row)
        layout.addWidget(viewer_card)

        # ---------------------------------------------------------------- AI masking
        ai = Card("AI Masking")
        model_row = QtWidgets.QHBoxLayout()
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setMinimumWidth(180)
        refresh = QtWidgets.QToolButton()
        refresh.setText("Refresh")
        refresh.setToolTip("Reload the installed models from the engine (install more in AI Studio)")
        refresh.clicked.connect(lambda: self.app.models.refresh())
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(refresh)
        model_widget = QtWidgets.QWidget()
        model_widget.setLayout(model_row)
        model_row.setContentsMargins(0, 0, 0, 0)
        self.auto_segment = check("Update mask after each prompt", True)
        ai.add_form([("Model", model_widget), ("", self.auto_segment)])
        self.segment_button = accent_button("Segment Frame", "Run the model on the prompt frame")
        self.segment_button.clicked.connect(self.segment_frame)
        ai.add(self.segment_button)
        self.ai_status = QtWidgets.QLabel("")
        self.ai_status.setObjectName("Dim")
        self.ai_status.setWordWrap(True)
        ai.add(self.ai_status)

        # ---------------------------------------------------------------- tracking
        tracking = Card("Mask Tracking")
        self.matte_name = QtWidgets.QLineEdit("roto_main")
        self.matte_name.setToolTip("Letters, digits, '_' or '-'. Mattes are written as <name>.####.png")
        self.range_start = spin(-100000, 1000000, 1001)
        self.range_end = spin(-100000, 1000000, 1100)
        range_widget = QtWidgets.QWidget()
        range_row = QtWidgets.QHBoxLayout(range_widget)
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.addWidget(self.range_start)
        range_row.addWidget(QtWidgets.QLabel("to"))
        range_row.addWidget(self.range_end)
        self.direction = combo([label for label, _ in DIRECTIONS])
        self.output_field = path_field("Default: <project>/roto/<name>", "Matte output folder", folder=True)
        tracking.add_form([("Name", self.matte_name), ("Frame range", range_widget), ("Direction", self.direction),
                           ("Output folder", self.output_field)])
        self.track_button = accent_button("Track Mask", "Propagate the prompted mask through the frame range")
        self.track_button.clicked.connect(self.track_mask)
        self.track_progress = QtWidgets.QProgressBar()
        self.track_progress.setRange(0, 100)
        self.track_progress.setValue(0)
        self.cancel_track_button = QtWidgets.QPushButton("Cancel")
        self.cancel_track_button.clicked.connect(self.cancel_track)
        track_row = QtWidgets.QHBoxLayout()
        track_row.addWidget(self.track_button)
        track_row.addWidget(self.track_progress, 1)
        track_row.addWidget(self.cancel_track_button)
        tracking.add_layout(track_row)
        self.track_status = QtWidgets.QLabel("")
        self.track_status.setObjectName("Dim")
        self.track_status.setWordWrap(True)
        tracking.add(self.track_status)
        layout.addLayout(grid([ai, tracking]))
        layout.addWidget(self._build_guide_card())

        # ---------------------------------------------------------------- refinement + manual
        refine = Card("Matte Refinement")
        self.grow = spin(-50, 50, 0, suffix=" px")
        self.grow.setToolTip("Grow (positive) or shrink (negative) the matte")
        self.feather = spin(0, 50, 0, 1, suffix=" px", step=0.5)
        self.fill_holes = check("Fill holes inside the subject")
        refine.add_form([("Shrink / grow", self.grow), ("Feather", self.feather), ("", self.fill_holes)])
        for widget in (self.grow, self.feather):
            widget.valueChanged.connect(self._schedule_auto)
        self.fill_holes.toggled.connect(self._schedule_auto)
        manual = Card("Manual Masking", badge="Later phase")
        manual.add_form([("Shape", combo(["Bezier", "B-Spline", "Freehand"])),
                         ("Motion blur", check("Shape motion blur"))])
        manual.add(future_button("Draw Shape", "a later phase"))
        layout.addLayout(grid([refine, manual]))

        self.bridge.job_updated.connect(self._on_job)
        self.bridge.connection_changed.connect(lambda _s: self._update_enabled())
        self.bridge.models_updated.connect(self._on_models)
        self._on_models({"models": self.app.models.models(), "error": ""})
        self.frame_spin.valueChanged.connect(lambda _f: self._update_enabled())
        if maya_bridge.is_maya_available():
            self.refresh_cameras()
        self._update_enabled()

    # ================================================================== Maya guides (Phase 03)
    def _build_guide_card(self) -> QtWidgets.QWidget:
        card = Card("Guide from Maya Tracking")
        intro = QtWidgets.QLabel("Use your own tracking in Maya: pick the tracked camera and the locators or "
                                 "geometry that follow the subject. They are projected onto the plate and keep "
                                 "the AI mask locked on. The scene is only read, never changed.")
        intro.setObjectName("Dim")
        intro.setWordWrap(True)
        card.add(intro)
        cam_row = QtWidgets.QHBoxLayout()
        self.guide_camera = QtWidgets.QComboBox()
        self.guide_camera.setMinimumWidth(200)
        self.refresh_cameras_button = QtWidgets.QToolButton()
        self.refresh_cameras_button.setText("Refresh")
        self.refresh_cameras_button.setToolTip("List the cameras in the Maya scene")
        self.refresh_cameras_button.clicked.connect(self.refresh_cameras)
        cam_row.setContentsMargins(0, 0, 0, 0)
        cam_row.addWidget(self.guide_camera, 1)
        cam_row.addWidget(self.refresh_cameras_button)
        cam_widget = QtWidgets.QWidget()
        cam_widget.setLayout(cam_row)
        self.include_list, include_widget = self._node_list("Locators / geometry on the subject")
        self.exclude_list, exclude_widget = self._node_list("Locators / geometry to keep out of the mask")
        self.guide_offset = spin(-100000, 100000, 0)
        self.guide_offset.setToolTip("Maya frame = plate frame + offset")
        self.guide_interval = spin(1, 100, 1, suffix=" frame(s)")
        self.guide_interval.setToolTip("Give the AI a guide every N frames (1 = every frame)")
        card.add_form([("Tracked camera", cam_widget), ("Subject (include)", include_widget),
                       ("Exclude", exclude_widget), ("Maya frame offset", self.guide_offset),
                       ("Guide every", self.guide_interval)])
        buttons = QtWidgets.QHBoxLayout()
        self.read_guides_button = accent_button("Read Tracking from Maya",
                                                "Sample the camera and objects over the plate's frames (read only)")
        self.read_guides_button.clicked.connect(self.read_guides)
        self.guide_prompt_button = QtWidgets.QPushButton("Use Guide as Prompt")
        self.guide_prompt_button.setToolTip("Copy the guide on the current frame into the prompts")
        self.guide_prompt_button.clicked.connect(self.use_guide_as_prompt)
        self.clear_guides_button = QtWidgets.QPushButton("Clear Guides")
        self.clear_guides_button.clicked.connect(self.clear_guides)
        for widget in (self.read_guides_button, self.guide_prompt_button, self.clear_guides_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        card.add_layout(buttons)
        toggles = QtWidgets.QHBoxLayout()
        self.use_guides = check("Use guides when tracking", True)
        self.show_guides = check("Show guides in viewer", True)
        self.show_guides.toggled.connect(lambda _on: self.show_frame(self.frame_spin.value()))
        toggles.addWidget(self.use_guides)
        toggles.addWidget(self.show_guides)
        toggles.addStretch(1)
        card.add_layout(toggles)
        self.guide_status = QtWidgets.QLabel("No guides. Read your tracking from Maya after loading the plate.")
        self.guide_status.setObjectName("Dim")
        self.guide_status.setWordWrap(True)
        card.add(self.guide_status)
        return card

    def _node_list(self, tooltip: str) -> Any:
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        nodes = QtWidgets.QListWidget()
        nodes.setToolTip(tooltip)
        nodes.setMaximumHeight(64)
        nodes.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        column = QtWidgets.QVBoxLayout()
        add = QtWidgets.QPushButton("Add Selected")
        add.setToolTip("Add the objects selected in Maya")
        remove = QtWidgets.QPushButton("Remove")
        add.clicked.connect(lambda: self._add_selected(nodes))
        remove.clicked.connect(lambda: [nodes.takeItem(nodes.row(i)) for i in nodes.selectedItems()])
        column.addWidget(add)
        column.addWidget(remove)
        row.addWidget(nodes, 1)
        row.addLayout(column)
        nodes.add_button = add  # type: ignore[attr-defined]
        return nodes, widget

    @staticmethod
    def _items(nodes: QtWidgets.QListWidget) -> List[str]:
        return [nodes.item(i).text() for i in range(nodes.count())]

    def _add_selected(self, nodes: QtWidgets.QListWidget) -> None:
        if not maya_bridge.is_maya_available():
            self.guide_status.setText("Maya scene not available (standalone preview).")
            return
        selected = maya_bridge.selected_transforms()
        if not selected:
            self.guide_status.setText("Select locators or geometry in Maya first.")
            return
        existing = set(self._items(self.include_list)) | set(self._items(self.exclude_list))
        for node in selected:
            if node not in existing:
                nodes.addItem(node)

    def refresh_cameras(self) -> None:
        if not maya_bridge.is_maya_available():
            self.guide_status.setText("Maya scene not available (standalone preview).")
            return
        current = self.guide_camera.currentText()
        self.guide_camera.clear()
        for camera in maya_bridge.list_cameras():
            self.guide_camera.addItem(camera)
        index = self.guide_camera.findText(current)
        if index >= 0:
            self.guide_camera.setCurrentIndex(index)

    def read_guides(self) -> None:
        if not self.plate:
            self.guide_status.setText("Load the plate first.")
            return
        if not maya_bridge.is_maya_available():
            self.guide_status.setText("Maya scene not available (standalone preview).")
            return
        camera = self.guide_camera.currentText()
        includes = self._items(self.include_list)
        if not camera or not includes:
            self.guide_status.setText("Choose the tracked camera and add at least one subject object.")
            return
        offset = int(self.guide_offset.value())
        frames = self.frames()
        try:
            scene = maya_bridge.read_guide_scene(camera, includes, self._items(self.exclude_list),
                                                 [f + offset for f in frames])
        except Exception as exc:  # scene read errors are shown, never raised into Maya
            self.guide_status.setText("Could not read the tracking: %s" % exc)
            return
        media = self.plate["media"]
        self.guides = mg.build_guides(scene, frames, int(media["width"]), int(media["height"]), offset)
        self.guide_source = "maya camera %s; include %s" % (camera.split("|")[-1],
                                                            ", ".join(n.split("|")[-1] for n in includes))
        missing = len(frames) - len(self.guides)
        self.guide_status.setText("Guides on %d of %d frames%s. Camera %s." % (
            len(self.guides), len(frames),
            (" (%d frame(s) with the subject off screen)" % missing) if missing else "", camera.split("|")[-1]))
        self.show_frame(self.frame_spin.value())
        self._update_enabled()

    def clear_guides(self) -> None:
        self.guides = {}
        self.guide_source = ""
        self.guide_status.setText("Guides cleared.")
        self.show_frame(self.frame_spin.value())
        self._update_enabled()

    def use_guide_as_prompt(self) -> None:
        frame = self.frame_spin.value()
        guide = self.guides.get(frame)
        if guide is None:
            self.guide_status.setText("No guide on frame %d." % frame)
            return
        self.prompts = rr.prompt_state_from_guide(frame, guide)
        self._prompts_changed()

    def _active_guides(self) -> Dict[int, Dict[str, Any]]:
        return self.guides if self.use_guides.isChecked() else {}

    # ================================================================== helpers
    @property
    def connected(self) -> bool:
        return self.app.connection.state == ConnectionState.CONNECTED

    def source(self) -> str:
        return self.source_field.line_edit.text().strip()

    def model_id(self) -> str:
        return str(self.model_combo.currentData() or "")

    def refine_settings(self) -> Dict[str, Any]:
        return {"grow_px": int(self.grow.value()), "feather_px": float(self.feather.value()),
                "fill_holes": bool(self.fill_holes.isChecked())}

    def frames(self) -> List[int]:
        return list(self.plate["frames"]) if self.plate else []

    def _project(self) -> Any:
        return self.app.projects.current

    def _submit(self, kind: str, request: Any) -> None:
        self._busy[kind] = "pending"
        self._relay.watch(self.app.jobs.submit(request), kind)
        self._update_enabled()

    def _update_enabled(self) -> None:
        connected = self.connected
        has_plate = self.plate is not None
        ready = connected and has_plate and bool(self.model_id()) and self.prompts.is_usable()
        guide_ready = (connected and has_plate and bool(self.model_id())
                       and self.frame_spin.value() in self._active_guides())
        self.load_button.setEnabled(connected and self._busy["proxies"] is None)
        self.segment_button.setEnabled(ready and self._busy["segment"] is None)
        self.track_button.setEnabled((ready or guide_ready) and self._busy["track"] is None)
        self.cancel_track_button.setEnabled(self._busy["track"] not in (None, "pending"))
        has_guides = bool(self.guides)
        self.guide_prompt_button.setEnabled(self.frame_spin.value() in self.guides)
        self.clear_guides_button.setEnabled(has_guides)
        self.read_guides_button.setEnabled(has_plate)
        self.undo_button.setEnabled(not self.prompts.is_empty())
        self.clear_button.setEnabled(not self.prompts.is_empty())
        if not connected:
            self.ai_status.setText("Engine not connected. Start it from the top bar.")
        elif not self.model_id():
            self.ai_status.setText("No segmentation model installed. Open AI Studio > Model Manager.")

    # ================================================================== plate
    def load_plate(self) -> None:
        source = self.source()
        if not source:
            self.plate_info.setText("Choose a plate first.")
            return
        if not os.path.isabs(source):
            self.plate_info.setText("Use an absolute path.")
            return
        self.plate_info.setText("Loading plate in the engine...")
        out = rr.proxy_dir(source, self._project())
        self._submit("proxies", rr.proxies_request(source, int(self.first_frame.value()), str(out)))

    def _plate_loaded(self, result: Dict[str, Any]) -> None:
        self.plate = result
        media = result["media"]
        frames = result["frames"]
        self.prompts.clear()
        self.preview.clear()
        self.track_output = None
        self.guides = {}
        self.guide_source = ""
        self.viewer.set_mask(None)
        self.viewer.set_prompts([], None)
        for widget in (self.frame_slider, self.frame_spin, self.range_start, self.range_end):
            widget.blockSignals(True)
            widget.setRange(frames[0], frames[-1])
            widget.blockSignals(False)
        self.range_start.setValue(frames[0])
        self.range_end.setValue(frames[-1])
        self.plate_info.setText("%s: %d frames (%d-%d), %dx%d%s" % (
            media["kind"], len(frames), frames[0], frames[-1], media["width"], media["height"],
            (", %.3g fps" % media["fps"]) if media.get("fps") else ""))
        self.frame_spin.setValue(frames[0])
        self.show_frame(frames[0])
        self._update_enabled()

    def show_frame(self, frame: int) -> None:
        if not self.plate:
            return
        proxy = Path(self.plate["proxy_dir"]) / ("proxy.%04d.jpg" % frame)
        image = QtGui.QImage(str(proxy)) if proxy.is_file() else None
        media = self.plate["media"]
        self.viewer.set_frame(image, (media["width"], media["height"]))
        on_prompt_frame = self.prompts.frame == frame
        self.viewer.set_prompts(self.prompts.points if on_prompt_frame else [],
                                self.prompts.box if on_prompt_frame else None)
        self.viewer.set_mask(self._mask_for(frame))
        guide = self.guides.get(frame) if self.show_guides.isChecked() else None
        self.viewer.set_guide([tuple(p) for p in guide["points"]] if guide else [],
                              tuple(guide["box"]) if guide and guide.get("box") else None)
        if self.guides:
            self.guide_prompt_button.setEnabled(frame in self.guides)
        hint = "Prompt frame %d" % self.prompts.frame if self.prompts.frame is not None else \
            "Click the subject to add prompts"
        self.viewer.set_message("Frame %d  |  %s" % (frame, hint))

    def _mask_for(self, frame: int) -> Optional[QtGui.QImage]:
        path: Optional[str] = None
        if self.track_output:
            candidate = rr.matte_path(self.track_output["output_dir"], self.track_output["name"], frame)
            if candidate.is_file():
                path = str(candidate)
        if path is None and frame == self.prompts.frame:
            path = self.preview.get(frame)
        if path and os.path.isfile(path):
            return QtGui.QImage(path)
        return None

    # ================================================================== prompts
    def _on_point(self, x: float, y: float, include: bool) -> None:
        self.prompts.add_point(self.frame_spin.value(), x, y, include)
        self._prompts_changed()

    def _on_box(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.prompts.set_box(self.frame_spin.value(), x0, y0, x1, y1)
        self._prompts_changed()

    def undo_prompt(self) -> None:
        self.prompts.undo()
        self._prompts_changed()

    def clear_prompts(self) -> None:
        self.prompts.clear()
        self.preview.clear()
        self._prompts_changed()

    def _prompts_changed(self) -> None:
        self.show_frame(self.frame_spin.value())
        self._update_enabled()
        self._schedule_auto()

    def _schedule_auto(self, *_args: Any) -> None:
        if self.auto_segment.isChecked() and self.prompts.is_usable() and self.plate is not None:
            self._auto_timer.start()

    def _auto_segment(self) -> None:
        if self._busy["segment"] is not None:
            self._segment_dirty = True  # run again with the latest prompts when the current job ends
            return
        if self.segment_button.isEnabled():
            self.segment_frame()

    def _toggle_mask(self, on: bool) -> None:
        self.viewer.show_mask = on
        self.viewer.update()

    # ================================================================== jobs
    def segment_frame(self) -> None:
        if not (self.plate and self.prompts.is_usable() and self.model_id()):
            return
        self._segment_dirty = False
        self.ai_status.setText("Segmenting frame %d..." % self.prompts.frame)
        self._submit("segment", rr.segment_request(
            self.source(), int(self.plate["media"]["first_frame"]), int(self.prompts.frame), self.model_id(),
            self.prompts, self.refine_settings(), str(rr.preview_dir(self.source(), self._project()))))

    def track_mask(self) -> None:
        guides = self._active_guides()
        if self.plate and not self.prompts.is_usable() and self.frame_spin.value() in guides:
            self.use_guide_as_prompt()  # start from the Maya guide on the current frame
        if not (self.plate and self.prompts.is_usable() and self.model_id()):
            return
        name = self.matte_name.text().strip()
        if not rr.valid_matte_name(name):
            self.track_status.setText("Invalid name: use letters, digits, '_' or '-'.")
            return
        output = self.output_field.line_edit.text().strip()
        if not output:
            default = rr.default_output_dir(name, self._project())
            if default is None:
                self.track_status.setText("Open a toolkit project or choose an output folder.")
                return
            output = str(default)
        start, end = int(self.range_start.value()), int(self.range_end.value())
        if not start <= int(self.prompts.frame) <= end:
            self.track_status.setText("The prompt frame (%d) must be inside the frame range." % self.prompts.frame)
            return
        direction = DIRECTIONS[self.direction.currentIndex()][1]
        targets = rr.frames_for_track(self.frames(), int(self.prompts.frame), start, end, direction)
        existing = rr.existing_mattes(output, name, targets)
        overwrite = False
        if existing:
            answer = QtWidgets.QMessageBox.question(
                self, "Overwrite mattes?",
                "%d matte file(s) named '%s' already exist in\n%s\n\nOverwrite them?" % (len(existing), name, output),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                self.track_status.setText("Tracking cancelled; existing mattes kept.")
                return
            overwrite = True
        self.track_output = {"output_dir": output, "name": name}
        self.track_progress.setValue(0)
        self.track_status.setText("Submitting tracking job...")
        self._submit("track", rr.track_request(
            self.source(), int(self.plate["media"]["first_frame"]), self.model_id(), self.prompts, start, end,
            direction, self.refine_settings(), output, name, overwrite,
            mg.guides_payload(guides) if guides else None, int(self.guide_interval.value()), self.guide_source))

    def cancel_track(self) -> None:
        job_id = self._busy.get("track")
        if job_id and job_id != "pending":
            self.app.jobs.cancel(job_id)

    def _on_submitted(self, kind: str, status: Any) -> None:
        if status is None:  # submission failed; the job manager logged why
            self._busy[kind] = None
            label = {"proxies": self.plate_info, "segment": self.ai_status, "track": self.track_status}[kind]
            label.setText("Request failed. See the log panel for details.")
            self._update_enabled()
            return
        self._busy[kind] = status.job_id
        self._pending[status.job_id] = kind
        latest = self.app.jobs.get(status.job_id) or status
        self._on_job(latest)
        self._update_enabled()

    def _on_job(self, status: Any) -> None:
        kind = self._pending.get(getattr(status, "job_id", ""))
        if kind is None:
            return
        if kind == "track" and status.state in (JobState.RUNNING, JobState.PAUSED, JobState.QUEUED):
            self.track_progress.setValue(int(status.progress * 100))
            self.track_status.setText(status.message or status.state.value)
            if status.message.startswith("Tracked frame") and self.plate:
                self.show_frame(self.frame_spin.value())
        if not status.is_terminal:
            return
        self._pending.pop(status.job_id, None)
        self._busy[kind] = None
        error = status.error.message if status.error else ""
        ok = status.state == JobState.COMPLETED and status.result is not None
        if kind == "proxies":
            if ok:
                self._plate_loaded(status.result)
            else:
                self.plate_info.setText("Could not load the plate: %s" % (error or status.state.value))
        elif kind == "segment":
            if ok:
                result = status.result
                self.preview[int(result["frame"])] = result["mask_path"]
                self.ai_status.setText("Mask ready (%s, %.0f%% of frame, %.2fs)" % (
                    result["backend"], result["coverage"] * 100, result["seconds"]))
                self.show_frame(self.frame_spin.value())
            else:
                self.ai_status.setText("Segmentation failed: %s" % (error or status.state.value))
            if self._segment_dirty:
                self._auto_timer.start()
        elif kind == "track":
            if ok:
                result = status.result
                self.track_progress.setValue(100)
                text = "Wrote %d mattes to %s (%s, %.1fs). Manifest: %s" % (
                    result["frames_written"], result["output_dir"], result["backend"], result["seconds"],
                    os.path.basename(result["manifest"]))
                if result.get("guided_frames"):
                    drift = result.get("drift_frames") or []
                    text += " Guided on %d frame(s)." % result["guided_frames"]
                    if drift:
                        text += " Check frames where the mask left the guide: %s%s" % (
                            ", ".join(str(f) for f in drift[:10]), "..." if len(drift) > 10 else "")
                self.track_status.setText(text)
            else:
                self.track_status.setText("Tracking %s: %s" % (status.state.value, error or "no mattes written"))
            self.show_frame(self.frame_spin.value())
        self._update_enabled()

    # ================================================================== models
    def _on_models(self, payload: Any) -> None:
        current = self.model_id()
        models = [m for m in (payload or {}).get("models", []) if m.installed and m.task == rr.SEGMENTATION_TASK]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        # Prefer real AI models over the classical fallback when both exist.
        for info in sorted(models, key=lambda m: (m.backend == "classical", m.name)):
            self.model_combo.addItem(info.name, info.model_id)
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)
        self._update_enabled()
