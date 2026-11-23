"""Phase 04: the Rotomation panel under MOCK Maya, with the REAL engine for the plate and the fit.

A FAKE scene holds a tracked camera, a Mixamo-named rig skeleton and a few controls. The body pose
file is written by the test (the real pose models are not available here), then the panel reads the
camera and rig (query only), the engine fits the rig, and the explicit bake / locator / FK-IK /
foot-lock buttons issue recorded write commands inside one undo chunk each. Not a test in Autodesk Maya.
"""
import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import pose_synthetic as ps  # noqa: E402
from qt_helpers import pump, pump_until  # noqa: E402
from maya_plugin.modules.body_tracking import retarget as rt  # noqa: E402
from maya_plugin.modules.body_tracking import roto_requests as rq  # noqa: E402
from shared.constants.engine_constants import ConnectionState  # noqa: E402

W, H = 640, 360
FRAMES = list(range(1001, 1007))
MIXAMO = {"pelvis": "Hips", "l_hip": "LeftUpLeg", "r_hip": "RightUpLeg", "l_knee": "LeftLeg", "r_knee": "RightLeg",
          "l_ankle": "LeftFoot", "r_ankle": "RightFoot", "l_toe": "LeftToeBase", "r_toe": "RightToeBase",
          "neck": "Neck", "head": "Head", "l_shoulder": "LeftArm", "r_shoulder": "RightArm",
          "l_elbow": "LeftForeArm", "r_elbow": "RightForeArm", "l_wrist": "LeftHand", "r_wrist": "RightHand"}


def translate(p, rot=(0.0, 0.0, 0.0)):
    return rt.matrix_from_euler_xyz(rot[0], rot[1], rot[2], p)


def build_scene(cmds, cams):
    cmds.add_node("|shotCam", "camera", lambda f: cams[int(round(f))]["world_matrix"], {
        "focalLength": lambda f: 35.0, "horizontalFilmAperture": 1.417, "verticalFilmAperture": 0.945,
        "filmFit": 1, "lensSqueezeRatio": 1.0, "horizontalFilmOffset": 0.0, "verticalFilmOffset": 0.0})
    cmds.add_node("|rig", "transform", lambda f: translate((0, 0, 0)))
    for body, name in MIXAMO.items():
        cmds.add_node("|rig|Hips" + ("" if body == "pelvis" else "|" + name), "joint",
                      lambda f, p=ps.REST[body]: translate(p))
    cmds.add_node("|rig|cog_ctrl", "transform", lambda f: translate((0, 102, 0), (0, 0, 0)))
    cmds.add_node("|rig|l_arm_fk_ctrl", "transform", lambda f: translate(ps.REST["l_shoulder"], (0, 0, -90)))
    cmds.add_node("|rig|l_foot_ik_ctrl", "transform", lambda f: translate(ps.REST["l_ankle"]))
    cmds.CHANNELS["|rig|l_arm_fk_ctrl.rotateY"] = {"lock": True}


def plate(folder):
    folder.mkdir(parents=True)
    for f in FRAMES:
        cv2.imwrite(str(folder / ("shot.%04d.png" % f)), np.full((H, W, 3), 60, np.uint8))
    return str(folder / "shot.####.png")


def write_pose(path, obs, source):
    frames = {str(f): {"bbox": [0, 0, W - 1, H - 1], "box_source": "frame", "kp2d": obs[f]["kp2d"],
                       "kp3d": obs[f]["kp3d"]} for f in FRAMES}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "pose_track", "version": 1, "source": source, "media": {"width": W, "height": H},
                                "joints": list(MIXAMO), "frames": frames}))
    return {"pose_file": str(path), "frames": len(FRAMES), "frame_range": [FRAMES[0], FRAMES[-1]], "has_3d": True,
            "weak_frames": [], "mean_confidence": 0.9, "seconds": 0.1}


def test_rotomation_workflow_under_mock_maya(qapp, toolkit, fake_cmds, tmp_path, monkeypatch):
    from maya_plugin.ui.qt import QtWidgets
    main = toolkit.main
    app = main.app
    toolkit.resize(1500, 1200)
    app.projects.create_project(str(tmp_path), "RotoShow")
    main.top_bar.start_button.click()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.CONNECTED, 30)
    truth = ps.walk(FRAMES)
    cams = {f: ps.camera_sample(f, FRAMES) for f in FRAMES}
    obs = ps.observations(truth, cams, W, H, noise_px=0.3)
    build_scene(fake_cmds, cams)
    main.show_module("body_tracking")
    panel = main.panel("body_tracking")
    panel.source_field.line_edit.setText(plate(tmp_path / "footage"))
    panel.load_button.click()
    assert pump_until(qapp, lambda: panel.plate is not None, 30), panel.plate_info.text()

    # A box drawn in the viewer marks the subject.
    panel.viewer.box_drawn.emit(10.0, 20.0, 300.0, 340.0)
    assert panel.subject == {"frame": 1001, "box": [10.0, 20.0, 300.0, 340.0]}

    # Body pose: written by the test (the job itself is covered by tests/integration/test_rotomation_http.py).
    name = panel.name()
    panel._pose_done(write_pose(rq.pose_file(str(panel.out_dir()), name), obs, panel.source()))
    assert panel.pose is not None and panel.viewer.has_pose()
    assert panel.fit_button.isEnabled()

    # Rig & camera: auto-map from the selected root, map three controls.
    panel.refresh_cameras()
    assert panel.camera_combo.currentText() == "|shotCam"
    fake_cmds.SELECTION[:] = ["|rig|Hips"]
    panel.auto_map_button.click()
    assert "Mapped 17 joints" in panel.rig_status.text(), panel.rig_status.text()
    rows = {driver: row for row, driver in enumerate(rt.DRIVERS)}
    for driver, node in (("pelvis", "|rig|cog_ctrl"), ("l_upperarm", "|rig|l_arm_fk_ctrl"),
                         ("l_foot_ik", "|rig|l_foot_ik_ctrl")):
        panel.control_table.item(rows[driver], 1).setText(node)
    panel.reference_frame.setValue(1001)
    panel.save_preset_button.click()
    preset_path = tmp_path / "RotoShow" / "rotomation" / "rigs" / "rig.rig.json"
    assert preset_path.is_file() and json.loads(preset_path.read_text())["joints"]["l_wrist"] == "|rig|Hips|LeftHand"

    # Fit (real engine). Reading camera + rig is query only.
    panel.fit_button.click()
    assert pump_until(qapp, lambda: "Fitted 6 frames" in panel.fit_status.text(), 120), panel.fit_status.text()
    assert fake_cmds.scene_modifying_calls() == []
    assert not any(n == "currentTime" for n, _a, _k in fake_cmds.CALLS)
    panel.frame_spin.setValue(1003)
    pump(qapp, 0.05)
    assert panel.viewer.has_fit()
    fit_doc = json.loads(rq.fit_file(str(panel.out_dir()), name).read_text())
    wrist = fit_doc["joints"].index("l_wrist")
    assert np.linalg.norm(np.array(fit_doc["frames"]["1003"]["p"][wrist]) - truth[1003]["l_wrist"]) < 6.0

    # Writes need an explicit button + confirmation, and each is one undo chunk.
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    panel.bake_mode.setCurrentIndex(2)  # all mapped controls
    panel.bake_button.click()
    assert "Baked 3 control(s) over 6 frames" in panel.apply_status.text(), panel.apply_status.text()
    assert fake_cmds.UNDO[:2] == ["open:AI Roto: bake fit to rig", "close"]
    constraints = [(a, k) for n, a, k in fake_cmds.CALLS if n == "parentConstraint"]
    by_target = {a[1]: k for a, k in constraints}
    assert by_target["|rig|l_arm_fk_ctrl"]["skipTranslate"] == ["x", "y", "z"]
    assert by_target["|rig|l_arm_fk_ctrl"]["skipRotate"] == ["y"]          # locked channel left alone
    assert "skipTranslate" not in by_target["|rig|cog_ctrl"]
    bake = next(k for n, _a, k in fake_cmds.CALLS if n == "bakeResults")
    assert bake["time"] == (1001, 1006) and bake["simulation"]
    # the temporary target locator for the COG holds offset * pelvis frame of the fit
    locator = next(a[0] for a, _k in constraints if a[1] == "|rig|cog_ctrl")
    positions = rt.fit_positions(fit_doc, 1004)
    expected = rt.mat_mul(rt.offset(translate((0, 102, 0)), rt.driver_frames(ps.REST)["pelvis"]),
                          rt.driver_frames(positions)["pelvis"])
    assert abs(fake_cmds.KEYS[locator]["ty"][1004.0] - expected[13]) < 1e-6
    assert any(n == "delete" for n, _a, _k in fake_cmds.CALLS)

    panel.skeleton_button.click()
    assert "keyed locators" in panel.apply_status.text(), panel.apply_status.text()
    assert len(fake_cmds.KEYS["%s_l_wrist_loc" % name]["tx"]) == 6

    # Foot lock over 1002-1004, holding frame 1002.
    panel.lock_control.setText("|rig|l_foot_ik_ctrl")
    panel.tool_start.setValue(1002)
    panel.tool_end.setValue(1004)
    panel.hold_frame.setValue(1002)
    panel.lock_button.click()
    assert "Locked l_foot_ik_ctrl" in panel.cleanup_status.text(), panel.cleanup_status.text()
    assert fake_cmds.UNDO[-2:] == ["open:AI Roto: lock foot", "close"]

    # FK/IK match on the left leg: record, then FK -> IK on the current frame.
    panel.limb_combo.setCurrentIndex(2)  # left leg
    panel.ik_field.setText("|rig|l_foot_ik_ctrl")
    panel.record_button.click()
    assert "Recorded the match pose" in panel.cleanup_status.text(), panel.cleanup_status.text()
    panel.match_ik_button.click()
    assert "Matched IK to FK pose on 1 frame(s)" in panel.cleanup_status.text(), panel.cleanup_status.text()
    main.top_bar.action_stop.trigger()
    assert pump_until(qapp, lambda: main.top_bar.status.state == ConnectionState.OFFLINE, 30)
