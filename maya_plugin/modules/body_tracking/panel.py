"""Body Tracking & Rotomation panel (Phase 04).

Workflow:

1. **Plate & subject** - load the plate (engine proxies), optionally draw a box around the person or
   point at a roto manifest from Roto Studio.
2. **Body pose (AI)** - the engine follows the person: 2D joints (RTMPose / MediaPipe) and,
   optionally, MediaPipe's 3D body as the starting pose.
3. **Rig & camera** - pick your tracked camera and map your rig's joints (auto-map by name) and the
   controls to bake. Saved as a rig preset in the project.
4. **Fit** - the engine fits *your rig's proportions* to the 2D joints *through your camera*, with
   the 3D body as a depth hint, smoothing and foot contacts.
5. **Apply** - explicit, undoable: keyed locators for the solve, and/or bake your controls.
6. **Cleanup tools** - FK/IK matching per limb and foot locking over a frame range.

Reading the scene is query-only. Every scene change is a button the artist presses, runs on Maya's
main thread and is one undo step.
"""
from __future__ import annotations

import os
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Optional

from maya_plugin.core import maya_bridge
from maya_plugin.modules.base import BaseModulePanel
from maya_plugin.modules.body_tracking import retarget as rt
from maya_plugin.modules.body_tracking import rig_mapping as rm
from maya_plugin.modules.body_tracking import roto_requests as rq
from maya_plugin.modules.body_tracking.viewer import PoseViewer
from maya_plugin.modules.roto_studio import maya_guides as mg
from maya_plugin.modules.roto_studio import roto_requests as rr
from maya_plugin.ui.components.widgets import Card, accent_button, check, combo, grid, path_field, spin
from maya_plugin.ui.qt import QtCore, QtGui, QtWidgets, Signal, is_valid
from shared.constants.engine_constants import ConnectionState, JobState

PLATE_FILTER = ("Plates (*.exr *.png *.jpg *.jpeg *.tif *.tiff *.dpx *.mov *.mp4 *.avi *.mxf *.mkv *.m4v);;"
                "All files (*.*)")
NONE_LABEL = "(none)"
BAKE_MODES = [("FK controls", "fk"), ("IK hands/feet + poles", "ik"), ("All mapped controls", "all")]


class _FutureRelay(QtCore.QObject):
    done = Signal(object, object)

    def watch(self, future: Future, kind: str) -> None:
        def callback(f: Future) -> None:
            if is_valid(self):
                try:
                    result = f.result()
                except Exception:
                    result = None
                self.done.emit(kind, result)
        future.add_done_callback(callback)


def _label(text: str = "") -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("Dim")
    label.setWordWrap(True)
    return label


def _row(*widgets: Any, stretch_last: bool = False) -> QtWidgets.QWidget:
    holder = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    if not stretch_last:
        layout.addStretch(1)
    return holder


class BodyTrackingPanel(BaseModulePanel):
    phase_label = "Phase 04 - Rotomation"
    subtitle = ("Track a person's body with AI, fit your own rig to it through your tracked Maya camera, "
                "then bake your controls and clean up with FK/IK matching and foot locks.")

    def build(self, layout: QtWidgets.QVBoxLayout) -> None:
        self.plate: Optional[Dict[str, Any]] = None
        self.pose: Optional[Dict[str, Any]] = None
        self.fit: Optional[Dict[str, Any]] = None
        self.subject: Optional[Dict[str, Any]] = None
        self.camera_samples: Dict[int, Any] = {}          # plate frame -> CameraSample (for the overlay)
        self.preset: Dict[str, Any] = rm.empty_preset()
        self._pending: Dict[str, str] = {}
        self._busy: Dict[str, Optional[str]] = {"proxies": None, "pose": None, "fit": None}
        self._relay = _FutureRelay(self)
        self._relay.done.connect(self._on_submitted)
        layout.addWidget(self._build_plate_card())
        layout.addLayout(grid([self._build_pose_card(), self._build_fit_card()]))
        layout.addWidget(self._build_rig_card())
        layout.addLayout(grid([self._build_apply_card(), self._build_cleanup_card()]))
        self.bridge.job_updated.connect(self._on_job)
        self.bridge.connection_changed.connect(lambda _s: self._update_enabled())
        self.bridge.models_updated.connect(self._on_models)
        self._on_models({"models": self.app.models.models(), "error": ""})
        if maya_bridge.is_maya_available():
            self.refresh_cameras()
        self._update_enabled()

    # ================================================================== cards
    def _build_plate_card(self) -> QtWidgets.QWidget:
        card = Card("Plate & Subject")
        self.source_field = path_field("Image sequence (shot.####.exr) or video file", "Select plate",
                                       file_filter=PLATE_FILTER)
        self.first_frame = spin(-100000, 1000000, 1001)
        self.matte_field = path_field("Optional: a Roto Studio manifest (<name>.roto.json) to follow the person",
                                      "Select roto manifest", file_filter="Roto manifest (*.roto.json);;All files (*.*)")
        card.add_form([("Source", self.source_field), ("Video start frame", self.first_frame),
                       ("Roto matte", self.matte_field)])
        self.load_button = accent_button("Load Plate", "Probe the plate and build viewer proxies in the engine")
        self.load_button.clicked.connect(self.load_plate)
        self.plate_info = _label("No plate loaded.")
        card.add_layout(self._hbox(self.load_button, self.plate_info, grow=1))
        self.viewer = PoseViewer(rm.DRAW_BONES)
        self.viewer.setMinimumHeight(320)
        self.viewer.box_drawn.connect(self._on_subject_box)
        card.add(self.viewer)
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setKeyboardTracking(False)
        self.frame_slider.valueChanged.connect(self.frame_spin.setValue)
        self.frame_spin.valueChanged.connect(self.frame_slider.setValue)
        self.frame_spin.valueChanged.connect(self.show_frame)
        self.show_pose = check("Show AI pose", True)
        self.show_fit = check("Show fitted rig", True)
        for box in (self.show_pose, self.show_fit):
            box.toggled.connect(lambda _on: self.show_frame(self.frame_spin.value()))
        self.clear_subject_button = QtWidgets.QPushButton("Clear Box")
        self.clear_subject_button.clicked.connect(self.clear_subject)
        frame_row = QtWidgets.QHBoxLayout()
        frame_row.addWidget(QtWidgets.QLabel("Frame"))
        frame_row.addWidget(self.frame_slider, 1)
        frame_row.addWidget(self.frame_spin)
        frame_row.addWidget(self.show_pose)
        frame_row.addWidget(self.show_fit)
        frame_row.addWidget(self.clear_subject_button)
        card.add_layout(frame_row)
        card.add(_label("Drag a box around the person on any frame (optional with one person in shot or a roto "
                        "matte). Green = AI pose, orange = your rig after the fit, seen through your camera."))
        return card

    def _build_pose_card(self) -> QtWidgets.QWidget:
        card = Card("Body Pose (AI)")
        self.pose_model = QtWidgets.QComboBox()
        self.body3d_model = QtWidgets.QComboBox()
        self.detector_model = QtWidgets.QComboBox()
        refresh = QtWidgets.QToolButton()
        refresh.setText("Refresh")
        refresh.clicked.connect(lambda: self.app.models.refresh())
        self.solve_name = QtWidgets.QLineEdit("body")
        self.solve_name.setToolTip("Letters, digits, '_' or '-'. Files go to <project>/rotomation/<name>/")
        self.range_start = spin(-100000, 1000000, 1001)
        self.range_end = spin(-100000, 1000000, 1100)
        card.add_form([("2D pose model", _row(self.pose_model, refresh, stretch_last=True)),
                       ("3D body model", self.body3d_model), ("Person detector", self.detector_model),
                       ("Name", self.solve_name),
                       ("Frame range", _row(self.range_start, QtWidgets.QLabel("to"), self.range_end))])
        self.pose_button = accent_button("Track Body", "Run the pose models over the frame range")
        self.pose_button.clicked.connect(self.track_body)
        self.pose_progress = QtWidgets.QProgressBar()
        self.pose_progress.setRange(0, 100)
        self.pose_cancel = QtWidgets.QPushButton("Cancel")
        self.pose_cancel.clicked.connect(lambda: self._cancel("pose"))
        card.add_layout(self._hbox(self.pose_button, self.pose_progress, self.pose_cancel, grow=1))
        self.pose_status = _label("")
        card.add(self.pose_status)
        return card

    def _build_rig_card(self) -> QtWidgets.QWidget:
        card = Card("Rig & Camera")
        card.add(_label("Your tracked camera and your rig. Joints give the proportions; controls are what gets "
                        "baked. The scene is only read here."))
        self.camera_combo = QtWidgets.QComboBox()
        self.camera_combo.setMinimumWidth(200)
        cam_refresh = QtWidgets.QToolButton()
        cam_refresh.setText("Refresh")
        cam_refresh.clicked.connect(self.refresh_cameras)
        self.frame_offset = spin(-100000, 100000, 0)
        self.frame_offset.setToolTip("Maya frame = plate frame + offset")
        self.reference_frame = spin(-100000, 1000000, 1001)
        self.reference_frame.setToolTip("A frame where your rig's controls and joints are in their matched pose "
                                        "(usually the bind/default pose, elbows and knees slightly bent)")
        ref_now = QtWidgets.QToolButton()
        ref_now.setText("Current")
        ref_now.clicked.connect(lambda: self.reference_frame.setValue(int(maya_bridge.current_frame()))
                                if maya_bridge.is_maya_available() else None)
        self.rig_name = QtWidgets.QLineEdit("rig")
        card.add_form([("Tracked camera", _row(self.camera_combo, cam_refresh, stretch_last=True)),
                       ("Maya frame offset", self.frame_offset),
                       ("Reference frame", _row(self.reference_frame, ref_now)), ("Rig preset name", self.rig_name)])
        # joints table
        self.joint_table = QtWidgets.QTableWidget(len(rm.BODY_JOINTS), 2)
        self.joint_table.setHorizontalHeaderLabels(["Body joint", "Rig joint"])
        self.joint_table.verticalHeader().setVisible(False)
        self.joint_table.horizontalHeader().setStretchLastSection(True)
        self.joint_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.joint_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.joint_table.setMinimumHeight(240)
        for row, joint in enumerate(rm.BODY_JOINTS):
            label = rm.JOINT_LABELS[joint] + ("" if joint in rm.REQUIRED_JOINTS else " (optional)")
            self.joint_table.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            self.joint_table.setItem(row, 1, QtWidgets.QTableWidgetItem(""))
        self.auto_map_button = QtWidgets.QPushButton("Auto-map from Selected Root")
        self.auto_map_button.setToolTip("Select the rig's root joint in Maya, then guess every body joint by name")
        self.auto_map_button.clicked.connect(self.auto_map)
        self.set_joint_button = QtWidgets.QPushButton("Set from Selection")
        self.set_joint_button.clicked.connect(lambda: self._set_from_selection(self.joint_table))
        self.clear_joint_button = QtWidgets.QPushButton("Clear")
        self.clear_joint_button.clicked.connect(lambda: self._clear_rows(self.joint_table))
        joints_box = QtWidgets.QVBoxLayout()
        joints_box.addWidget(self.joint_table)
        joints_box.addLayout(self._hbox(self.auto_map_button, self.set_joint_button, self.clear_joint_button))
        # controls table
        drivers = list(rt.DRIVERS)
        self.control_table = QtWidgets.QTableWidget(len(drivers), 3)
        self.control_table.setHorizontalHeaderLabels(["Driver", "Control", "Channels"])
        self.control_table.verticalHeader().setVisible(False)
        self.control_table.horizontalHeader().setStretchLastSection(False)
        self.control_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.control_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.control_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.control_table.setMinimumHeight(240)
        for row, driver in enumerate(drivers):
            label, channels = rt.DRIVERS[driver]
            self.control_table.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            self.control_table.setItem(row, 1, QtWidgets.QTableWidgetItem(""))
            box = combo(["tr", "r", "t"])
            box.setCurrentText(channels)
            box.setToolTip("t = translate, r = rotate")
            self.control_table.setCellWidget(row, 2, box)
        self.set_control_button = QtWidgets.QPushButton("Set from Selection")
        self.set_control_button.clicked.connect(lambda: self._set_from_selection(self.control_table))
        self.clear_control_button = QtWidgets.QPushButton("Clear")
        self.clear_control_button.clicked.connect(lambda: self._clear_rows(self.control_table))
        controls_box = QtWidgets.QVBoxLayout()
        controls_box.addWidget(self.control_table)
        controls_box.addLayout(self._hbox(self.set_control_button, self.clear_control_button))
        tables = QtWidgets.QHBoxLayout()
        tables.addLayout(joints_box, 1)
        tables.addLayout(controls_box, 1)
        card.add_layout(tables)
        self.save_preset_button = QtWidgets.QPushButton("Save Rig Preset")
        self.save_preset_button.clicked.connect(self.save_preset)
        self.load_preset_button = QtWidgets.QPushButton("Load Rig Preset...")
        self.load_preset_button.clicked.connect(self.load_preset_dialog)
        self.rig_status = _label("")
        card.add_layout(self._hbox(self.save_preset_button, self.load_preset_button, self.rig_status, grow=2))
        return card

    def _build_fit_card(self) -> QtWidgets.QWidget:
        card = Card("Fit to Your Camera")
        self.prior_weight = spin(0.0, 20.0, 2.0, 2, step=0.25)
        self.prior_weight.setToolTip("How much the 3D body model's pose counts against the 2D joints (depth hint)")
        self.smooth_weight = spin(0.0, 100.0, 8.0, 1, step=1.0)
        self.smooth_weight.setToolTip("Higher = smoother motion, lower = follows the 2D joints more tightly")
        self.foot_lock = check("Lock planted feet (no sliding, stay above the ground)", True)
        self.ground_auto = check("Estimate ground height from the feet", True)
        self.ground_height = spin(-1e6, 1e6, 0.0, 3)
        self.ground_height.setToolTip("World height of the ground in your scene (cm)")
        self.ground_auto.toggled.connect(lambda on: self.ground_height.setEnabled(not on))
        self.ground_height.setEnabled(False)
        card.add_form([("3D body influence", self.prior_weight), ("Smoothing", self.smooth_weight),
                       ("", self.foot_lock), ("", self.ground_auto), ("Ground height", self.ground_height)])
        self.fit_button = accent_button("Fit Rig to Camera", "Read the camera and rig (read only) and run the fit")
        self.fit_button.clicked.connect(self.run_fit)
        self.fit_progress = QtWidgets.QProgressBar()
        self.fit_progress.setRange(0, 100)
        self.fit_cancel = QtWidgets.QPushButton("Cancel")
        self.fit_cancel.clicked.connect(lambda: self._cancel("fit"))
        card.add_layout(self._hbox(self.fit_button, self.fit_progress, self.fit_cancel, grow=1))
        self.fit_status = _label("")
        card.add(self.fit_status)
        return card

    def _build_apply_card(self) -> QtWidgets.QWidget:
        card = Card("Apply to Maya")
        card.add(_label("These change your scene. Each is one undo step (Ctrl+Z)."))
        self.bake_mode = combo([label for label, _ in BAKE_MODES])
        card.add_form([("Bake", self.bake_mode)])
        self.skeleton_button = QtWidgets.QPushButton("Create Solve Locators")
        self.skeleton_button.setToolTip("Keyed locators at the fitted joints, in a new group")
        self.skeleton_button.clicked.connect(self.create_skeleton)
        self.bake_button = accent_button("Bake to Rig Controls", "Bake the fit onto the mapped controls")
        self.bake_button.clicked.connect(self.bake_fit)
        card.add_layout(self._hbox(self.skeleton_button, self.bake_button))
        self.apply_status = _label("")
        card.add(self.apply_status)
        return card

    def _build_cleanup_card(self) -> QtWidgets.QWidget:
        card = Card("Cleanup Tools")
        self.limb_combo = combo([rm.LIMB_LABELS[k] for k in rm.LIMBS])
        self.limb_combo.currentIndexChanged.connect(self._show_limb)
        self.fk_fields = [QtWidgets.QLineEdit() for _ in range(3)]
        for field, part in zip(self.fk_fields, ("upper", "middle", "end")):
            field.setPlaceholderText("FK %s control" % part)
        self.ik_field = QtWidgets.QLineEdit()
        self.ik_field.setPlaceholderText("IK hand / foot control")
        self.pole_field = QtWidgets.QLineEdit()
        self.pole_field.setPlaceholderText("Pole vector control")
        self.switch_field = QtWidgets.QLineEdit()
        self.switch_field.setPlaceholderText("node.ikFkBlend (optional)")
        self.switch_fk = spin(-1000, 1000, 0.0, 2)
        self.switch_ik = spin(-1000, 1000, 1.0, 2)
        pick = QtWidgets.QPushButton("Fill from Selection")
        pick.setToolTip("Select FK upper, FK middle, FK end, IK control, pole control in Maya (in that order)")
        pick.clicked.connect(self._fill_limb_from_selection)
        card.add_form([("Limb", self.limb_combo), ("FK controls", _row(*self.fk_fields, stretch_last=True)),
                       ("IK control", self.ik_field), ("Pole vector", self.pole_field),
                       ("IK/FK switch", _row(self.switch_field, QtWidgets.QLabel("FK"), self.switch_fk,
                                             QtWidgets.QLabel("IK"), self.switch_ik, stretch_last=True)),
                       ("", pick)])
        self.record_button = QtWidgets.QPushButton("Record Match Pose")
        self.record_button.setToolTip("On a frame where FK and IK line up (e.g. the bind pose), store the offsets")
        self.record_button.clicked.connect(self.record_match_pose)
        self.match_ik_button = QtWidgets.QPushButton("FK -> IK")
        self.match_ik_button.setToolTip("Move the IK control and pole to the limb's current pose")
        self.match_ik_button.clicked.connect(lambda: self.match_limb("ik"))
        self.match_fk_button = QtWidgets.QPushButton("IK -> FK")
        self.match_fk_button.setToolTip("Rotate the FK controls to the limb's current pose")
        self.match_fk_button.clicked.connect(lambda: self.match_limb("fk"))
        self.match_range = check("Over the frame range below (else current frame)")
        card.add_layout(self._hbox(self.record_button, self.match_ik_button, self.match_fk_button))
        card.add(self.match_range)
        self.lock_control = QtWidgets.QLineEdit()
        self.lock_control.setPlaceholderText("Foot control (IK foot)")
        lock_pick = QtWidgets.QToolButton()
        lock_pick.setText("<")
        lock_pick.setToolTip("Use the selected control")
        lock_pick.clicked.connect(self._pick_lock_control)
        self.tool_start = spin(-100000, 1000000, 1001)
        self.tool_end = spin(-100000, 1000000, 1010)
        self.hold_frame = spin(-100000, 1000000, 1001)
        self.lock_rotate = check("Hold rotation too", True)
        card.add_form([("Frame range", _row(self.tool_start, QtWidgets.QLabel("to"), self.tool_end)),
                       ("Foot lock", _row(self.lock_control, lock_pick, stretch_last=True)),
                       ("Hold pose of frame", _row(self.hold_frame, self.lock_rotate))])
        self.lock_button = QtWidgets.QPushButton("Lock Foot")
        self.lock_button.clicked.connect(self.lock_foot)
        card.add(self.lock_button)
        self.cleanup_status = _label("")
        card.add(self.cleanup_status)
        self._current_limb = list(rm.LIMBS)[0]
        return card

    @staticmethod
    def _hbox(*widgets: Any, grow: Optional[int] = None) -> QtWidgets.QHBoxLayout:
        """Widgets in a row; ``grow`` is the index of the one that takes the spare width."""
        row = QtWidgets.QHBoxLayout()
        for index, widget in enumerate(widgets):
            row.addWidget(widget, 1 if index == grow else 0)
        if grow is None:
            row.addStretch(1)
        return row

    # ================================================================== helpers
    @property
    def connected(self) -> bool:
        return self.app.connection.state == ConnectionState.CONNECTED

    def source(self) -> str:
        return self.source_field.line_edit.text().strip()

    def name(self) -> str:
        return self.solve_name.text().strip()

    def frames(self) -> List[int]:
        return list(self.plate["frames"]) if self.plate else []

    def out_dir(self) -> Path:
        return rq.output_dir(self.name(), self.app.projects.current)

    def _maya(self, status: QtWidgets.QLabel) -> bool:
        if not maya_bridge.is_maya_available():
            status.setText("Maya scene not available (standalone preview).")
            return False
        return True

    def _submit(self, kind: str, request: Any) -> None:
        self._busy[kind] = "pending"
        self._relay.watch(self.app.jobs.submit(request), kind)
        self._update_enabled()

    def _cancel(self, kind: str) -> None:
        job_id = self._busy.get(kind)
        if job_id and job_id != "pending":
            self.app.jobs.cancel(job_id)

    def _confirm(self, title: str, text: str) -> bool:
        answer = QtWidgets.QMessageBox.question(self, title, text, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                                                QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def _update_enabled(self) -> None:
        connected = self.connected
        self.load_button.setEnabled(connected and self._busy["proxies"] is None)
        self.pose_button.setEnabled(connected and self.plate is not None and bool(self.pose_model.currentData())
                                    and self._busy["pose"] is None)
        self.pose_cancel.setEnabled(self._busy["pose"] not in (None, "pending"))
        self.fit_button.setEnabled(connected and self.pose is not None and self._busy["fit"] is None)
        self.fit_cancel.setEnabled(self._busy["fit"] not in (None, "pending"))
        has_fit = self.fit is not None
        self.skeleton_button.setEnabled(has_fit)
        self.bake_button.setEnabled(has_fit)
        self.clear_subject_button.setEnabled(self.subject is not None)
        if not connected:
            self.pose_status.setText("Engine not connected. Start it from the top bar.")
        elif not self.pose_model.count():
            self.pose_status.setText("No pose model installed. Open AI Studio > Model Manager "
                                     "(RTMPose or MediaPipe; needs the -WithPose engine setup).")

    # ================================================================== models
    def _on_models(self, payload: Any) -> None:
        models = [m for m in (payload or {}).get("models", []) if m.installed]

        def fill(box: QtWidgets.QComboBox, task: str, optional: bool) -> None:
            current = box.currentData()
            box.blockSignals(True)
            box.clear()
            if optional:
                box.addItem(NONE_LABEL, "")
            for info in sorted((m for m in models if rq.provides(m, task)),
                               key=lambda m: (m.backend != "rtmpose", m.name)):
                box.addItem(info.name, info.model_id)
            index = box.findData(current)
            if index >= 0:
                box.setCurrentIndex(index)
            elif optional and task == rq.BODY3D_TASK and box.count() > 1:
                box.setCurrentIndex(1)  # use the 3D body model by default when installed
            box.blockSignals(False)

        fill(self.pose_model, "pose_2d", False)
        fill(self.body3d_model, rq.BODY3D_TASK, True)
        fill(self.detector_model, rq.DETECTOR_TASK, True)
        self._update_enabled()

    # ================================================================== plate + subject
    def load_plate(self) -> None:
        source = self.source()
        if not source or not os.path.isabs(source):
            self.plate_info.setText("Choose a plate (absolute path) first.")
            return
        self.plate_info.setText("Loading plate in the engine...")
        out = rr.proxy_dir(source, self.app.projects.current)
        self._submit("proxies", rr.proxies_request(source, int(self.first_frame.value()), str(out)))

    def _plate_loaded(self, result: Dict[str, Any]) -> None:
        self.plate = result
        self.pose = self.fit = self.subject = None
        frames = result["frames"]
        for widget in (self.frame_slider, self.frame_spin, self.range_start, self.range_end):
            widget.blockSignals(True)
            widget.setRange(frames[0], frames[-1])
            widget.blockSignals(False)
        self.range_start.setValue(frames[0])
        self.range_end.setValue(frames[-1])
        self.reference_frame.setValue(frames[0])
        self.tool_start.setValue(frames[0])
        self.tool_end.setValue(min(frames[-1], frames[0] + 10))
        self.hold_frame.setValue(frames[0])
        media = result["media"]
        self.plate_info.setText("%s: %d frames (%d-%d), %dx%d" % (media["kind"], len(frames), frames[0], frames[-1],
                                                                 media["width"], media["height"]))
        self.frame_spin.setValue(frames[0])
        self.show_frame(frames[0])
        self._update_enabled()

    def _on_subject_box(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.subject = {"frame": int(self.frame_spin.value()),
                        "box": [round(min(x0, x1), 1), round(min(y0, y1), 1), round(max(x0, x1), 1),
                                round(max(y0, y1), 1)]}
        self.pose_status.setText("Subject box set on frame %d." % self.subject["frame"])
        self.show_frame(self.frame_spin.value())
        self._update_enabled()

    def clear_subject(self) -> None:
        self.subject = None
        self.show_frame(self.frame_spin.value())
        self._update_enabled()

    def show_frame(self, frame: int) -> None:
        if not self.plate:
            return
        proxy = Path(self.plate["proxy_dir"]) / ("proxy.%04d.jpg" % frame)
        image = QtGui.QImage(str(proxy)) if proxy.is_file() else None
        media = self.plate["media"]
        self.viewer.set_frame(image, (media["width"], media["height"]))
        entry = (self.pose or {}).get("frames", {}).get(str(frame)) if self.show_pose.isChecked() else None
        self.viewer.set_pose(entry["kp2d"] if entry else [])
        self.viewer.set_fit(self._projected_fit(frame) if self.show_fit.isChecked() else [])
        on_subject = self.subject is not None and self.subject["frame"] == frame
        self.viewer.set_subject_box(self.subject["box"] if on_subject else (entry or {}).get("bbox"))
        self.viewer.set_message("Frame %d" % frame)

    def _projected_fit(self, frame: int) -> List[Any]:
        if not self.fit or frame not in self.camera_samples or not self.plate:
            return []
        positions = rt.fit_positions(self.fit, frame)
        if not positions:
            return []
        cam = self.camera_samples[frame]
        try:
            inverse = mg.mat_inverse(cam.world_matrix)
        except ValueError:
            return []
        media = self.plate["media"]
        out = []
        for joint in rm.BODY_JOINTS:
            point = positions.get(joint)
            out.append(mg.project(point, cam, inverse, int(media["width"]), int(media["height"]))
                       if point is not None else None)
        return out

    # ================================================================== pose
    def track_body(self) -> None:
        if not self.plate:
            return
        name = self.name()
        if not rr.valid_matte_name(name):
            self.pose_status.setText("Invalid name: use letters, digits, '_' or '-'.")
            return
        out = self.out_dir()
        overwrite = False
        if rq.pose_file(str(out), name).exists():
            if not self._confirm("Overwrite body pose?", "%s already exists.\n\nOverwrite it?"
                                 % rq.pose_file(str(out), name)):
                self.pose_status.setText("Kept the existing body pose.")
                return
            overwrite = True
        matte = self.matte_field.line_edit.text().strip() or None
        start, end = int(self.range_start.value()), int(self.range_end.value())
        subject = self.subject if self.subject and start <= self.subject["frame"] <= end else None
        self.pose_progress.setValue(0)
        self.pose_status.setText("Submitting body pose job...")
        self._submit("pose", rq.pose_request(
            self.source(), int(self.plate["media"]["first_frame"]), str(self.pose_model.currentData()), str(out), name,
            body3d_model_id=str(self.body3d_model.currentData() or "") or None,
            detector_model_id=str(self.detector_model.currentData() or "") or None,
            subject=subject, matte_manifest=matte, start=start, end=end, overwrite=overwrite))

    # ================================================================== rig mapping
    def refresh_cameras(self) -> None:
        if not self._maya(self.rig_status):
            return
        current = self.camera_combo.currentText()
        self.camera_combo.clear()
        for camera in maya_bridge.list_cameras():
            self.camera_combo.addItem(camera)
        index = self.camera_combo.findText(current)
        if index >= 0:
            self.camera_combo.setCurrentIndex(index)

    def _set_from_selection(self, table: QtWidgets.QTableWidget) -> None:
        if not self._maya(self.rig_status):
            return
        selected = maya_bridge.selected_transforms()
        rows = sorted({i.row() for i in table.selectedIndexes()})
        if not selected or not rows:
            self.rig_status.setText("Select a row here and the node in Maya.")
            return
        # one row: the first selected node; several rows: selected nodes in order
        for row, node in zip(rows, selected):
            table.item(row, 1).setText(node)
        self._read_tables()

    def _clear_rows(self, table: QtWidgets.QTableWidget) -> None:
        for index in table.selectedIndexes():
            table.item(index.row(), 1).setText("")
        self._read_tables()

    def auto_map(self) -> None:
        if not self._maya(self.rig_status):
            return
        selected = maya_bridge.selected_transforms()
        if not selected:
            self.rig_status.setText("Select the rig's root joint (or hips) in Maya first.")
            return
        mapping = rm.auto_map(maya_bridge.joint_hierarchy(selected[0]))
        for row, joint in enumerate(rm.BODY_JOINTS):
            if joint in mapping:
                self.joint_table.item(row, 1).setText(mapping[joint])
        self._read_tables()
        missing = rm.missing_joints(self.preset)
        self.rig_status.setText("Mapped %d joints by name.%s Check them before fitting." % (
            len(mapping), (" Still missing: %s." % ", ".join(rm.JOINT_LABELS[j] for j in missing)) if missing else ""))

    def _read_tables(self) -> Dict[str, Any]:
        joints = {joint: self.joint_table.item(row, 1).text().strip() for row, joint in enumerate(rm.BODY_JOINTS)}
        controls = {}
        for row, driver in enumerate(rt.DRIVERS):
            node = self.control_table.item(row, 1).text().strip()
            if node:
                controls[driver] = {"node": node, "channels": self.control_table.cellWidget(row, 2).currentText()}
        self.preset["joints"] = {k: v for k, v in joints.items() if v}
        self.preset["controls"] = controls
        self.preset["name"] = self.rig_name.text().strip() or "rig"
        self.preset["reference_frame"] = int(self.reference_frame.value())
        self._store_limb()
        return self.preset

    def _write_tables(self) -> None:
        for row, joint in enumerate(rm.BODY_JOINTS):
            self.joint_table.item(row, 1).setText(self.preset["joints"].get(joint, ""))
        for row, driver in enumerate(rt.DRIVERS):
            spec = self.preset["controls"].get(driver) or {}
            self.control_table.item(row, 1).setText(spec.get("node", ""))
            self.control_table.cellWidget(row, 2).setCurrentText(spec.get("channels") or rt.DRIVERS[driver][1])
        self.rig_name.setText(self.preset.get("name") or "rig")
        if self.preset.get("reference_frame") is not None:
            self.reference_frame.setValue(int(self.preset["reference_frame"]))
        self._show_limb()

    def save_preset(self) -> None:
        self._read_tables()
        name = self.preset["name"]
        if not rr.valid_matte_name(name):
            self.rig_status.setText("Invalid preset name: use letters, digits, '_' or '-'.")
            return
        path = rq.rig_preset_dir(self.app.projects.current) / ("%s.rig.json" % name)
        rm.save_preset(self.preset, path)
        self.rig_status.setText("Saved %s" % path)

    def load_preset_dialog(self) -> None:
        start = str(rq.rig_preset_dir(self.app.projects.current))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load rig preset", start, "Rig preset (*.rig.json)")
        if path:
            self.load_preset(path)

    def load_preset(self, path: str) -> None:
        try:
            self.preset = rm.load_preset(Path(path))
        except ValueError as exc:
            self.rig_status.setText(str(exc))
            return
        self._write_tables()
        self.rig_status.setText("Loaded rig preset '%s'." % self.preset["name"])

    # ================================================================== fit
    def run_fit(self) -> None:
        if not self.pose or not self._maya(self.fit_status):
            return
        preset = self._read_tables()
        missing = rm.missing_joints(preset)
        camera = self.camera_combo.currentText()
        if missing or not camera:
            self.fit_status.setText("Choose the tracked camera and map all required joints (missing: %s)."
                                    % (", ".join(rm.JOINT_LABELS[j] for j in missing) or "none"))
            return
        offset = int(self.frame_offset.value())
        plate_frames = sorted(int(f) for f in self.pose["frames"])
        reference = int(self.reference_frame.value())
        try:
            samples = maya_bridge.read_camera_samples(camera, [f + offset for f in plate_frames])
            matrices = maya_bridge.read_world_matrices(list(preset["joints"].values()), [reference])
            up = maya_bridge.up_axis()
        except Exception as exc:
            self.fit_status.setText("Could not read the scene: %s" % exc)
            return
        self.camera_samples = {f: samples[f + offset] for f in plate_frames}
        rest = rq.positions_from_matrices(preset["joints"], matrices, reference)
        name = self.name()
        out = self.out_dir()
        fit_input = rq.build_fit_input(camera, self.camera_samples, preset["name"], reference, rest, up, offset)
        input_path = rq.write_json(rq.fit_input_file(str(out), name), fit_input)
        overwrite = False
        if rq.fit_file(str(out), name).exists():
            if not self._confirm("Overwrite fit?", "%s already exists.\n\nOverwrite it?" % rq.fit_file(str(out), name)):
                self.fit_status.setText("Kept the existing fit.")
                return
            overwrite = True
        options = {"prior_weight": float(self.prior_weight.value()), "smooth_weight": float(self.smooth_weight.value()),
                   "foot_lock": bool(self.foot_lock.isChecked()),
                   "ground_height": None if self.ground_auto.isChecked() else float(self.ground_height.value())}
        self.fit_progress.setValue(0)
        self.fit_status.setText("Submitting fit job...")
        self._submit("fit", rq.fit_request(self.pose["_path"], str(input_path), str(out), name, options,
                                           overwrite=overwrite))

    # ================================================================== apply
    def create_skeleton(self) -> None:
        if not self.fit or not self._maya(self.apply_status):
            return
        if not self._confirm("Create solve locators?", "This adds a group of keyed locators to your scene "
                             "(one undo step)."):
            return
        positions = {int(f) + int(self.fit.get("_offset", 0)): entry["p"] for f, entry in self.fit["frames"].items()}
        try:
            group = maya_bridge.create_solve_skeleton(self.name(), self.fit["joints"], positions)
        except Exception as exc:
            self.apply_status.setText("Could not create the locators: %s" % exc)
            return
        self.apply_status.setText("Created %s with %d keyed locators." % (group, len(self.fit["joints"])))

    def bake_fit(self) -> None:
        if not self.fit or not self._maya(self.apply_status):
            return
        preset = self._read_tables()
        mode = BAKE_MODES[self.bake_mode.currentIndex()][1]
        controls = rq.controls_for_mode(preset["controls"], mode)
        if not controls:
            self.apply_status.setText("Map at least one control for this bake mode in Rig & Camera.")
            return
        reference = int(self.reference_frame.value())
        offset = int(self.frame_offset.value())
        try:
            nodes = list(preset["joints"].values()) + [s["node"] for s in controls.values()]
            matrices = maya_bridge.read_world_matrices(nodes, [reference])
        except Exception as exc:
            self.apply_status.setText("Could not read the rig: %s" % exc)
            return
        rest = rq.positions_from_matrices(preset["joints"], matrices, reference)
        control_ref = {s["node"]: matrices[s["node"]][reference] for s in controls.values()}
        offsets = rq.control_offsets(controls, control_ref, rest)
        plate_targets = rq.fit_targets(self.fit, controls, offsets)
        targets = {node: {"frames": {f + offset: m for f, m in spec["frames"].items()}, "channels": spec["channels"]}
                   for node, spec in plate_targets.items()}
        frames = sorted({f for spec in targets.values() for f in spec["frames"]})
        if not frames:
            self.apply_status.setText("Nothing to bake.")
            return
        if not self._confirm("Bake to rig controls?", "Bake %d control(s) over Maya frames %d-%d?\n\nExisting keys "
                             "in that range are replaced on the baked channels. One undo step." % (
                                 len(targets), frames[0], frames[-1])):
            return
        try:
            result = maya_bridge.bake_world_targets(targets, frames, "AI Roto: bake fit to rig")
        except Exception as exc:
            self.apply_status.setText("Bake failed: %s" % exc)
            return
        text = "Baked %d control(s) over %d frames." % (len(result["baked"]), result["frames"])
        if result["skipped"]:
            text += " Skipped: %s." % ", ".join("%s (%s)" % (rm.short_name(k), v) for k, v in result["skipped"].items())
        self.apply_status.setText(text)

    # ================================================================== cleanup tools
    def _store_limb(self) -> None:
        limb = self.preset.setdefault("limbs", {}).setdefault(self._current_limb, {})
        limb.update({"fk": [f.text().strip() for f in self.fk_fields], "ik": self.ik_field.text().strip(),
                     "pole": self.pole_field.text().strip(), "switch": self.switch_field.text().strip(),
                     "switch_fk": float(self.switch_fk.value()), "switch_ik": float(self.switch_ik.value())})

    def _show_limb(self, *_args: Any) -> None:
        if hasattr(self, "_current_limb") and self.sender() is self.limb_combo:
            self._store_limb()
        self._current_limb = list(rm.LIMBS)[self.limb_combo.currentIndex()]
        limb = (self.preset.get("limbs") or {}).get(self._current_limb) or {}
        fk = (limb.get("fk") or ["", "", ""]) + ["", "", ""]
        for field, value in zip(self.fk_fields, fk):
            field.setText(value)
        self.ik_field.setText(limb.get("ik", ""))
        self.pole_field.setText(limb.get("pole", ""))
        self.switch_field.setText(limb.get("switch", ""))
        self.switch_fk.setValue(float(limb.get("switch_fk", 0.0)))
        self.switch_ik.setValue(float(limb.get("switch_ik", 1.0)))
        self.cleanup_status.setText("Match pose recorded." if limb.get("offsets") else "")

    def _fill_limb_from_selection(self) -> None:
        if not self._maya(self.cleanup_status):
            return
        selected = maya_bridge.selected_transforms()
        for field, node in zip(self.fk_fields + [self.ik_field, self.pole_field], selected):
            field.setText(node)
        self._store_limb()

    def _pick_lock_control(self) -> None:
        if self._maya(self.cleanup_status):
            selected = maya_bridge.selected_transforms()
            if selected:
                self.lock_control.setText(selected[0])

    def _tool_frames(self) -> List[int]:
        if self.match_range.isChecked():
            start, end = int(self.tool_start.value()), int(self.tool_end.value())
            return list(range(min(start, end), max(start, end) + 1))
        return [int(maya_bridge.current_frame())]

    def record_match_pose(self) -> None:
        if not self._maya(self.cleanup_status):
            return
        self._read_tables()
        cfg = rq.limb_config(self.preset, self._current_limb)
        if not all(cfg["joints"]):
            self.cleanup_status.setText("Map this limb's three joints in Rig & Camera first.")
            return
        controls = [c for c in cfg.get("fk", []) if c] + [c for c in (cfg.get("ik"),) if c]
        if not controls:
            self.cleanup_status.setText("Enter the limb's FK and/or IK controls.")
            return
        frame = int(maya_bridge.current_frame())
        try:
            matrices = maya_bridge.read_world_matrices(cfg["joints"] + controls, [frame])
        except Exception as exc:
            self.cleanup_status.setText("Could not read the limb: %s" % exc)
            return
        offsets = rq.record_match({**cfg}, {j: matrices[j][frame] for j in cfg["joints"]},
                                  {c: matrices[c][frame] for c in controls})
        self.preset["limbs"][self._current_limb]["offsets"] = offsets
        self.cleanup_status.setText("Recorded the match pose of %s on frame %d. Save the rig preset to keep it."
                                    % (rm.LIMB_LABELS[self._current_limb], frame))

    def match_limb(self, direction: str) -> None:
        if not self._maya(self.cleanup_status):
            return
        self._read_tables()
        cfg = rq.limb_config(self.preset, self._current_limb)
        frames = self._tool_frames()
        try:
            joint_m = maya_bridge.read_world_matrices(cfg["joints"], frames)
            targets = rq.match_targets(cfg, direction, joint_m, frames)
        except Exception as exc:
            self.cleanup_status.setText("Cannot match: %s" % exc)
            return
        label = "AI Roto: match %s %s" % (rm.LIMB_LABELS[self._current_limb], "FK -> IK" if direction == "ik"
                                          else "IK -> FK")
        try:
            result = maya_bridge.bake_world_targets(targets, frames, label)
            if cfg.get("switch"):
                value = cfg.get("switch_ik", 1.0) if direction == "ik" else cfg.get("switch_fk", 0.0)
                maya_bridge.key_attribute(cfg["switch"], frames, float(value), label + " (switch)")
        except Exception as exc:
            self.cleanup_status.setText("Match failed: %s" % exc)
            return
        self.cleanup_status.setText("Matched %s on %d frame(s): %s." % (
            "IK to FK pose" if direction == "ik" else "FK to IK pose", len(frames),
            ", ".join(rm.short_name(c) for c in result["baked"]) or "nothing baked"))

    def lock_foot(self) -> None:
        if not self._maya(self.cleanup_status):
            return
        control = self.lock_control.text().strip()
        if not control:
            self.cleanup_status.setText("Choose the foot control to lock.")
            return
        start, end = int(self.tool_start.value()), int(self.tool_end.value())
        frames = list(range(min(start, end), max(start, end) + 1))
        hold = int(self.hold_frame.value())
        try:
            matrix = maya_bridge.read_world_matrices([control], [hold])[control][hold]
        except Exception as exc:
            self.cleanup_status.setText("Cannot read %s: %s" % (control, exc))
            return
        targets = rq.foot_lock_targets(control, matrix, frames, self.lock_rotate.isChecked())
        try:
            result = maya_bridge.bake_world_targets(targets, frames, "AI Roto: lock foot")
        except Exception as exc:
            self.cleanup_status.setText("Foot lock failed: %s" % exc)
            return
        self.cleanup_status.setText("Locked %s to its frame %d pose over %d-%d.%s" % (
            rm.short_name(control), hold, frames[0], frames[-1],
            "" if result["baked"] else " Nothing baked: %s" % result["skipped"].get(control, "")))

    # ================================================================== jobs
    def _on_submitted(self, kind: str, status: Any) -> None:
        labels = {"proxies": self.plate_info, "pose": self.pose_status, "fit": self.fit_status}
        if status is None:
            self._busy[kind] = None
            labels[kind].setText("Request failed. See the log panel for details.")
            self._update_enabled()
            return
        self._busy[kind] = status.job_id
        self._pending[status.job_id] = kind
        self._on_job(self.app.jobs.get(status.job_id) or status)
        self._update_enabled()

    def _on_job(self, status: Any) -> None:
        kind = self._pending.get(getattr(status, "job_id", ""))
        if kind is None:
            return
        progress = {"pose": self.pose_progress, "fit": self.fit_progress}.get(kind)
        labels = {"proxies": self.plate_info, "pose": self.pose_status, "fit": self.fit_status}
        if progress is not None and status.state in (JobState.RUNNING, JobState.PAUSED, JobState.QUEUED):
            progress.setValue(int(status.progress * 100))
            labels[kind].setText(status.message or status.state.value)
        if not status.is_terminal:
            return
        self._pending.pop(status.job_id, None)
        self._busy[kind] = None
        ok = status.state == JobState.COMPLETED and status.result is not None
        error = status.error.message if status.error else status.state.value
        if kind == "proxies":
            if ok:
                self._plate_loaded(status.result)
            else:
                self.plate_info.setText("Could not load the plate: %s" % error)
        elif kind == "pose":
            if ok:
                self._pose_done(status.result)
            else:
                self.pose_status.setText("Body pose %s: %s" % (status.state.value, error))
        elif kind == "fit":
            if ok:
                self._fit_done(status.result)
            else:
                self.fit_status.setText("Fit %s: %s" % (status.state.value, error))
        self._update_enabled()

    def _pose_done(self, result: Dict[str, Any]) -> None:
        try:
            self.pose = rq.read_json(Path(result["pose_file"]))
        except (OSError, ValueError) as exc:
            self.pose_status.setText("Could not read %s: %s" % (result["pose_file"], exc))
            return
        self.pose["_path"] = result["pose_file"]
        self.fit = None
        self.pose_progress.setValue(100)
        weak = result.get("weak_frames") or []
        self.pose_status.setText("Body pose on %d frames (mean confidence %.2f%s)%s. %s" % (
            result["frames"], result["mean_confidence"], ", with 3D body" if result.get("has_3d") else "",
            (". Low confidence on %d frame(s), e.g. %s" % (len(weak), ", ".join(str(f) for f in weak[:6])))
            if weak else "", os.path.basename(result["pose_file"])))
        self.show_frame(self.frame_spin.value())
        self._update_enabled()

    def _fit_done(self, result: Dict[str, Any]) -> None:
        try:
            self.fit = rq.read_json(Path(result["fit_file"]))
        except (OSError, ValueError) as exc:
            self.fit_status.setText("Could not read %s: %s" % (result["fit_file"], exc))
            return
        self.fit["_offset"] = int(self.frame_offset.value())
        self.fit_progress.setValue(100)
        flagged = result.get("flagged_frames") or []
        contacts = result.get("contact_frames") or {}
        text = "Fitted %d frames, mean error %s px. Feet planted on %d (L) / %d (R) frames." % (
            result["frames"], result["mean_error_px"], contacts.get("l", 0), contacts.get("r", 0))
        if flagged:
            text += " Check frames over %s px: %s%s" % (result["flag_threshold_px"],
                                                       ", ".join(str(f) for f in flagged[:10]),
                                                       "..." if len(flagged) > 10 else "")
        if result.get("skipped_no_camera"):
            text += " %d frame(s) had no camera sample." % result["skipped_no_camera"]
        self.fit_status.setText(text)
        self.show_frame(self.frame_spin.value())
        self._update_enabled()
