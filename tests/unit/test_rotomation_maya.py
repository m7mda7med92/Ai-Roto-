"""Phase 04 Maya-side helpers (pure Python): rig auto-mapping, presets, driver frames, bake/match maths."""
import json
import math
import random

import pytest

from maya_plugin.modules.body_tracking import retarget as rt
from maya_plugin.modules.body_tracking import rig_mapping as rm
from maya_plugin.modules.body_tracking import roto_requests as rq
from maya_plugin.modules.roto_studio import maya_guides as mg

REST = {"pelvis": (0.0, 100.0, 0.0), "l_hip": (9.0, 95.0, 0.0), "r_hip": (-9.0, 95.0, 0.0),
        "l_knee": (9.5, 52.0, 3.0), "r_knee": (-9.5, 52.0, 3.0), "l_ankle": (10.0, 9.0, -2.0),
        "r_ankle": (-10.0, 9.0, -2.0), "l_toe": (10.5, 2.0, 12.0), "r_toe": (-10.5, 2.0, 12.0),
        "neck": (0.0, 150.0, -1.0), "head": (0.0, 165.0, 1.0), "l_shoulder": (17.0, 146.0, -2.0),
        "r_shoulder": (-17.0, 146.0, -2.0), "l_elbow": (20.0, 118.0, -6.0), "r_elbow": (-20.0, 118.0, -6.0),
        "l_wrist": (22.0, 93.0, 2.0), "r_wrist": (-22.0, 93.0, 2.0)}


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def rigid(rx, ry, rz, t):
    return rt.matrix_from_euler_xyz(rx, ry, rz, t)


def apply(m, p):
    return mg.transform_point(p, m)


# ------------------------------------------------------------------------------------------ mapping
@pytest.mark.parametrize("names", [
    ["mixamorig:" + n for n in ("Hips", "Spine", "Spine1", "Neck", "Head", "LeftShoulder", "LeftArm", "LeftForeArm",
                                "LeftHand", "RightShoulder", "RightArm", "RightForeArm", "RightHand", "LeftUpLeg",
                                "LeftLeg", "LeftFoot", "LeftToeBase", "RightUpLeg", "RightLeg", "RightFoot",
                                "RightToeBase")],
    ["Root_M", "Hip_L", "Knee_L", "Ankle_L", "Toes_L", "Hip_R", "Knee_R", "Ankle_R", "Toes_R", "Spine1_M", "Chest_M",
     "Neck_M", "Head_M", "Scapula_L", "Shoulder_L", "Elbow_L", "Wrist_L", "Scapula_R", "Shoulder_R", "Elbow_R",
     "Wrist_R"],
    ["C_pelvis_jnt", "L_thigh_jnt", "L_knee_jnt", "L_ankle_jnt", "L_ball_jnt", "R_thigh_jnt", "R_knee_jnt",
     "R_ankle_jnt", "R_ball_jnt", "C_neck_01_jnt", "C_head_jnt", "L_clavicle_jnt", "L_upperarm_jnt", "L_elbow_jnt",
     "L_wrist_jnt", "R_clavicle_jnt", "R_upperarm_jnt", "R_elbow_jnt", "R_wrist_jnt"],
])
def test_auto_map_common_rigs(names):
    mapping = rm.auto_map(["|rig|" + n for n in names])
    assert set(mapping) == set(rm.BODY_JOINTS)
    shorts = {k: rm.short_name(v) for k, v in mapping.items()}
    assert "Shoulder" not in shorts["l_shoulder"] or shorts["l_shoulder"] == "Shoulder_L"  # not Mixamo's clavicle
    assert "clavicle" not in shorts["l_shoulder"] and "Scapula" not in shorts["r_shoulder"]
    assert rm.classify(mapping["l_knee"])[0] == "l"


def test_preset_round_trip_and_validation(tmp_path):
    preset = rm.empty_preset("hero")
    preset["joints"] = {"pelvis": "|hips", "bogus": "|x"}
    preset["controls"] = {"pelvis": {"node": "|cog_ctrl", "channels": "tr"}}
    path = rm.save_preset(preset, tmp_path / "rigs" / "hero.rig.json")
    loaded = rm.load_preset(path)
    assert loaded["joints"] == {"pelvis": "|hips"} and loaded["controls"]["pelvis"]["node"] == "|cog_ctrl"
    assert "l_knee" in rm.missing_joints(loaded)
    (tmp_path / "bad.json").write_text(json.dumps({"kind": "other"}))
    with pytest.raises(ValueError):
        rm.load_preset(tmp_path / "bad.json")


# ------------------------------------------------------------------------------------------ maths
def test_euler_round_trip():
    random.seed(4)
    for _ in range(50):
        angles = (random.uniform(-170, 170), random.uniform(-85, 85), random.uniform(-170, 170))
        m = rt.matrix_from_euler_xyz(*angles)
        assert close(rt.euler_xyz(m), angles, 1e-6)
    assert close(rt.unwrap((350.0, 0.0, -170.0), (-9.0, 0.0, 185.0)), (351.0, 0.0, -175.0))


def test_driver_frames_are_orthonormal_and_follow_the_body():
    frames = rt.driver_frames(REST)
    for name, m in frames.items():
        rows = [m[0:3], m[4:7], m[8:11]]
        if name.endswith("_pole"):
            continue
        for i in range(3):
            assert math.isclose(rt.length(rows[i]), 1.0, abs_tol=1e-9)
            for j in range(i + 1, 3):
                assert abs(rt.dot(rows[i], rows[j])) < 1e-9
        assert rt.dot(rt.cross(rows[0], rows[1]), rows[2]) > 0.999  # right-handed
    assert close(rt.translation(frames["l_forearm"]), REST["l_elbow"])
    # the knee pole sits in front of the knee (the leg bends forward)
    assert rt.translation(frames["l_leg_pole"])[2] > REST["l_knee"][2] + 20


def test_offsets_reproduce_controls_and_follow_a_rigid_move():
    control_ref = {"|cog": rigid(5, 10, -3, (0, 105, 2)), "|l_arm_fk": rigid(0, 0, -80, (17, 146, -2)),
                   "|l_hand_ik": rigid(10, 20, 30, (22, 93, 2)), "|l_leg_pv": rigid(0, 0, 0, (9, 50, 60))}
    controls = {"pelvis": {"node": "|cog"}, "l_upperarm": {"node": "|l_arm_fk"},
                "l_hand_ik": {"node": "|l_hand_ik"}, "l_leg_pole": {"node": "|l_leg_pv"}}
    offsets = rq.control_offsets(controls, control_ref, REST)
    move = rigid(0, 120, 0, (300, 0, -50))
    moved = {j: apply(move, p) for j, p in REST.items()}
    fit = {"joints": list(REST), "frames": {"1001": {"p": [list(REST[j]) for j in REST]},
                                            "1002": {"p": [list(moved[j]) for j in REST]}}}
    targets = rq.fit_targets(fit, controls, offsets)
    for node in ("|cog", "|l_arm_fk", "|l_hand_ik"):
        assert close(targets[node]["frames"][1001], control_ref[node], 1e-6)
        assert close(targets[node]["frames"][1002], rt.mat_mul(control_ref[node], move), 1e-6)
    assert targets["|cog"]["channels"] == "tr" and targets["|l_arm_fk"]["channels"] == "r"
    # poles go to the pole point (in front of the moved knee), translation only
    pole = rt.translation(targets["|l_leg_pv"]["frames"][1002])
    knee = moved["l_knee"]
    forward = rt.unit(rt.sub(apply(move, (0, 0, 1)), apply(move, (0, 0, 0))))
    assert rt.dot(rt.sub(pole, knee), forward) > 20 and targets["|l_leg_pv"]["channels"] == "t"
    assert set(rq.controls_for_mode(controls, "fk")) == {"pelvis", "l_upperarm"}
    assert set(rq.controls_for_mode(controls, "ik")) == {"pelvis", "l_hand_ik", "l_leg_pole"}


def test_fk_ik_match_targets():
    joints = ["|shoulder", "|elbow", "|wrist"]
    ref = {"|shoulder": rigid(0, 0, -90, REST["l_shoulder"]), "|elbow": rigid(0, 10, -95, REST["l_elbow"]),
           "|wrist": rigid(0, 0, -90, REST["l_wrist"])}
    controls = {"|fk1": rigid(0, 0, -90, (17, 146, -2)), "|fk2": rigid(3, 10, -95, (20, 118, -6)),
                "|fk3": rigid(0, 0, -90, (22, 93, 2)), "|ik": rigid(0, 0, 0, (22, 93, 2))}
    limb = {"joints": joints, "fk": ["|fk1", "|fk2", "|fk3"], "ik": "|ik", "pole": "|pv"}
    limb["offsets"] = rq.record_match(limb, ref, controls)
    pose = rigid(30, -20, 10, (5, -3, 8))
    joint_m = {j: {1010: rt.mat_mul(ref[j], pose)} for j in joints}
    fk = rq.match_targets(limb, "fk", joint_m, [1010])
    for control in ("|fk1", "|fk2", "|fk3"):
        assert close(fk[control]["frames"][1010], rt.mat_mul(controls[control], pose), 1e-6)
        assert fk[control]["channels"] == "r"
    ik = rq.match_targets(limb, "ik", joint_m, [1010])
    assert close(ik["|ik"]["frames"][1010], rt.mat_mul(controls["|ik"], pose), 1e-6)
    assert ik["|pv"]["channels"] == "t"
    with pytest.raises(ValueError):
        rq.match_targets({"joints": joints}, "fk", joint_m, [1010])
    lock = rq.foot_lock_targets("|foot", controls["|ik"], [1, 2, 3], rotate=False)
    assert lock["|foot"]["channels"] == "t" and len(lock["|foot"]["frames"]) == 3


def test_requests_and_fit_input(tmp_path):
    sample = mg.CameraSample(world_matrix=rigid(0, 0, 0, (0, 0, 500)), focal_mm=35, h_aperture_in=1.4,
                             v_aperture_in=0.9)
    doc = rq.build_fit_input("|cam", {1001: sample}, "hero", 1001, REST, "y", 0)
    assert doc["kind"] == "roto_fit_input" and doc["camera"]["frames"]["1001"]["focal_mm"] == 35
    request = rq.pose_request("/p/shot.####.exr", 1001, "rtmpose_m_halpe26", "/out", "body",
                              body3d_model_id="mediapipe_pose_heavy", subject={"frame": 1001, "box": [0, 0, 9, 9]})
    assert request.job_type == "pose_track" and "detector_model_id" not in request.params
    fit = rq.fit_request("/out/body.pose.json", "/out/body.fitinput.json", "/out", "body", {"foot_lock": True})
    assert fit.job_type == "roto_fit" and fit.params["options"] == {"foot_lock": True}
    assert rq.output_dir("body").name == "body"
