"""Roto Studio + Model Manager under MOCK Maya (fake maya.cmds, offscreen Qt) with a REAL engine process.

Uses the built-in OpenCV backend (CPU) on a synthetic plate. This is not a test inside
Autodesk Maya and does not exercise SAM 2 (weights cannot be downloaded in CI).
"""
import importlib

import pytest

pytest.importorskip("cv2")

from helpers import iou, synthetic_plate  # noqa: E402
from qt_helpers import pump, pump_until  # noqa: E402
from maya_plugin.ui import qt  # noqa: E402
from shared.constants.engine_constants import ConnectionState  # noqa: E402

QtTest = importlib.import_module("%s.QtTest" % qt.QT_BINDING)


def start_engine(qapp, main):
    main.top_bar.start_button.click()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.CONNECTED, 30)


def click_source(qapp, viewer, x, y, button=qt.QtCore.Qt.LeftButton, modifier=qt.QtCore.Qt.NoModifier):
    point = viewer.to_widget(x, y).toPoint()
    QtTest.QTest.mouseClick(viewer, button, modifier, point)
    pump(qapp, 0.05)


def test_roto_studio_point_prompt_segment_and_track(qapp, toolkit, fake_cmds, tmp_path, monkeypatch):
    import cv2
    main = toolkit.main
    app = main.app
    toolkit.resize(1400, 1000)
    app.projects.create_project(str(tmp_path), "RotoShow")
    start_engine(qapp, main)
    main.show_module("roto_studio")
    panel = main.panel("roto_studio")
    assert type(panel).__name__ == "RotoStudioPanel"
    assert pump_until(qapp, lambda: panel.model_combo.count() >= 1, 10)
    assert panel.model_id() == "opencv_grabcut"
    assert not panel.segment_button.isEnabled()  # no plate / prompts yet

    pattern, truth = synthetic_plate(tmp_path / "footage", count=6, step=8)
    panel.source_field.line_edit.setText(pattern)
    panel.load_button.click()
    assert pump_until(qapp, lambda: panel.plate is not None, 30), panel.plate_info.text()
    assert panel.frame_slider.minimum() == 1001 and panel.frame_slider.maximum() == 1006
    assert "6 frames" in panel.plate_info.text()
    proxy_dir = tmp_path / "RotoShow" / "cache" / "roto_proxies"
    assert len(list(proxy_dir.glob("*/proxy.*.jpg"))) == 6

    # Real mouse clicks in the viewer: include on the disc, exclude (right-click) on background.
    viewer = panel.viewer
    pump(qapp, 0.1)
    assert viewer.width() > 100 and not viewer.image_rect().isEmpty()
    click_source(qapp, viewer, 106, 90)
    click_source(qapp, viewer, 300, 20, qt.QtCore.Qt.RightButton)
    assert panel.prompts.frame == 1001 and [p[2] for p in panel.prompts.points] == [1, 0]
    assert abs(panel.prompts.points[0][0] - 106) < 3 and abs(panel.prompts.points[0][1] - 90) < 3

    # Auto-update segments after the prompts; the overlay appears.
    assert pump_until(qapp, lambda: 1001 in panel.preview and viewer.has_mask(), 30), panel.ai_status.text()
    preview = cv2.imread(panel.preview[1001], cv2.IMREAD_UNCHANGED)
    assert iou(preview > 127, truth[1001]) > 0.9
    assert "Mask ready" in panel.ai_status.text()

    # Track through the shot into <project>/roto/<name>.
    panel.matte_name.setText("disc")
    assert panel.track_button.isEnabled()
    panel.track_button.click()
    out = tmp_path / "RotoShow" / "roto" / "disc"
    assert pump_until(qapp, lambda: "Wrote 6 mattes" in panel.track_status.text(), 60), panel.track_status.text()
    assert panel.track_progress.value() == 100
    for frame, mask in truth.items():
        assert iou(cv2.imread(str(out / ("disc.%04d.png" % frame)), cv2.IMREAD_UNCHANGED) > 127, mask) > 0.85
    panel.frame_spin.setValue(1005)
    pump(qapp, 0.05)
    assert viewer.has_mask()  # tracked matte shown on other frames

    # Re-tracking asks before overwriting; answering No keeps the files.
    before = (out / "disc.1001.png").stat().st_mtime_ns
    monkeypatch.setattr(qt.QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: qt.QtWidgets.QMessageBox.No))
    panel.track_button.click()
    pump(qapp, 0.2)
    assert "existing mattes kept" in panel.track_status.text()
    assert (out / "disc.1001.png").stat().st_mtime_ns == before

    # Prompts tools.
    panel.undo_button.click()
    assert len(panel.prompts.points) == 1
    panel.clear_button.click()
    assert panel.prompts.is_empty() and not panel.segment_button.isEnabled()

    main.top_bar.action_stop.trigger()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 30)
    assert not panel.load_button.isEnabled()
    # Roto Studio never issues a scene-modifying Maya command.
    assert fake_cmds.scene_modifying_calls() == []


def test_box_prompt_via_ctrl_drag(qapp, toolkit, tmp_path):
    main = toolkit.main
    toolkit.resize(1400, 1000)
    main.show_module("roto_studio")
    panel = main.panel("roto_studio")
    panel._plate_loaded({"media": {"kind": "sequence", "width": 320, "height": 180, "first_frame": 1001,
                                   "fps": None}, "frames": [1001, 1002], "proxy_dir": str(tmp_path)})
    pump(qapp, 0.1)
    viewer = panel.viewer
    a, b = viewer.to_widget(60, 40).toPoint(), viewer.to_widget(160, 140).toPoint()
    QtTest.QTest.mousePress(viewer, qt.QtCore.Qt.LeftButton, qt.QtCore.Qt.ControlModifier, a)
    QtTest.QTest.mouseMove(viewer, b)
    QtTest.QTest.mouseRelease(viewer, qt.QtCore.Qt.LeftButton, qt.QtCore.Qt.ControlModifier, b)
    pump(qapp, 0.05)
    box = panel.prompts.box
    assert box is not None and abs(box[0] - 60) < 3 and abs(box[3] - 140) < 3
    assert panel.prompts.is_usable()
    panel.box_mode.setChecked(True)
    assert viewer.mode == "box"


def test_model_manager_lists_catalog(qapp, toolkit, fake_cmds):
    main = toolkit.main
    start_engine(qapp, main)
    main.show_module("ai_studio")
    widget = main.panel("ai_studio").model_manager
    assert pump_until(qapp, lambda: widget.table.rowCount() == 5, 10)
    names = [widget.table.item(r, 0).text() for r in range(5)]
    assert "OpenCV GrabCut (CPU)" in names or any("GrabCut" in n for n in names)
    builtin_row = next(r for r in range(5) if "GrabCut" in widget.table.item(r, 0).text())
    widget.table.selectRow(builtin_row)
    assert not widget.install_button.isEnabled() and not widget.remove_button.isEnabled()
    sam_row = next(r for r in range(5) if "tiny" in widget.table.item(r, 0).text().lower())
    widget.table.selectRow(sam_row)
    assert widget.install_button.isEnabled() and not widget.remove_button.isEnabled()
    assert widget.table.item(sam_row, 4).text() == "Not installed"
    assert "Ready" in widget.table.item(builtin_row, 4).text()
    assert "GrabCut" in main.right_panel.models_label.text()
    main.top_bar.action_stop.trigger()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 30)
    assert fake_cmds.scene_modifying_calls() == []
