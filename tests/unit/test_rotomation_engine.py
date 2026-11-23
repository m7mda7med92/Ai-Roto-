"""Phase 04 engine side: skeleton layouts, camera maths, the rig fit and model archives (no models needed)."""
import json
import math
import zipfile

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import pose_synthetic as ps  # noqa: E402
from ai_engine.pose import fit  # noqa: E402
from ai_engine.pose import skeleton as sk  # noqa: E402
from ai_engine.pose.camera import camera_from_dict  # noqa: E402

W, H = 1920, 1080


def test_layouts_map_onto_body_joints():
    halpe = [[float(i), float(100 + i)] for i in range(26)]
    body = sk.to_body(halpe, [0.9] * 26, "halpe26")
    assert len(body) == len(sk.JOINTS)
    assert body[sk.INDEX["pelvis"]][:2] == [11.5, 111.5]           # mid hips
    assert body[sk.INDEX["l_toe"]][:2] == [21.0, 121.0]            # big + small toe
    coco = sk.to_body(halpe[:17], [0.8] * 17, "coco17")
    assert coco[sk.INDEX["l_toe"]][-1] == 0.0 and coco[sk.INDEX["neck"]][:2] == [5.5, 105.5]
    mp33 = sk.to_body([[i, i, -i] for i in range(33)], [1.0] * 33, "mediapipe33")
    assert mp33[sk.INDEX["l_ankle"]] == [27.0, 27.0, -27.0, 1.0]
    assert sk.bbox_from_body(body, pad=0.0) == [3.5, 103.5, 22.0, 122.0]  # head = mid ears


def test_engine_and_maya_use_the_same_body():
    from maya_plugin.modules.body_tracking import rig_mapping as rm
    assert rm.BODY_JOINTS == sk.JOINTS and rm.PARENT == sk.PARENT and set(rm.REQUIRED_JOINTS) == set(sk.REQUIRED)


def test_numpy_projection_matches_maya_guides():
    from maya_plugin.modules.roto_studio import maya_guides as mg
    sample = ps.camera_sample(1010, list(range(1001, 1030)))
    sample.update(film_fit=0, h_offset_in=0.05, v_offset_in=-0.02, lens_squeeze=1.1)
    cam = camera_from_dict(sample, W, H)
    maya_cam = mg.CameraSample(**sample)
    inverse = mg.mat_inverse(maya_cam.world_matrix)
    for point in ([0.0, 100.0, 0.0], [30.0, 150.0, -20.0], [-40.0, 10.0, 25.0]):
        uv, depth = cam.project(np.array(point))
        expected = mg.project(point, maya_cam, inverse, W, H)
        assert depth > 0 and np.allclose(uv, expected, atol=1e-6)
        # the ray through that pixel passes through the point
        ray = cam.ray(*uv)
        assert np.allclose(cam.position + ray * depth, point, atol=1e-6)


def test_body_model_keeps_rig_bone_lengths():
    model = fit.BodyModel(ps.REST)
    state = model.initial_state({j: np.array(p) for j, p in ps.REST.items()})
    pos = model.positions(state[None])[0]
    assert np.allclose(pos, model.rest, atol=1e-6)
    rng = np.random.default_rng(3)
    moved = model.positions(state[None] + rng.normal(0, 0.5, (1, model.n_vars)))[0]
    for (a, b), length in zip(model.limbs, model.lengths):
        assert abs(np.linalg.norm(moved[model.index[b]] - moved[model.index[a]]) - length) < 1e-6
    with pytest.raises(ValueError):
        fit.BodyModel({k: v for k, v in ps.REST.items() if k != "l_knee"})


def _error(result, truth, model):
    return np.array([[np.linalg.norm(result.positions[i, k] - truth[f][j]) for k, j in enumerate(model.joints)]
                     for i, f in enumerate(result.frames)])


def test_fit_recovers_walk_through_moving_camera():
    frames = list(range(1001, 1017))
    truth = ps.walk(frames)
    cams = {f: ps.camera_sample(f, frames) for f in frames}
    model, data = ps.fit_frames(truth, cams, W, H, noise_px=1.5)
    result = fit.RotoFitter(model, data, fit.FitOptions()).run()
    error = _error(result, truth, model)
    assert error.mean() < 3.0, error.mean()            # cm, on a 1.7 m character ~5 m away
    assert np.nanmean(result.reprojection_px) < 3.0
    assert result.frames == frames


def test_fit_without_3d_prior_still_lands_on_the_2d():
    frames = list(range(1001, 1007))
    truth = ps.walk(frames)
    cams = {f: ps.camera_sample(f, frames) for f in frames}
    model, data = ps.fit_frames(truth, cams, W, H, noise_px=0.5, prior=False)
    result = fit.RotoFitter(model, data, fit.FitOptions(global_pass=False)).run()
    assert np.nanmean(result.reprojection_px) < 2.0
    pelvis = model.index["pelvis"]
    depth_error = [abs(np.linalg.norm(result.positions[i, pelvis] - truth[f]["pelvis"])) for i, f in enumerate(frames)]
    assert max(depth_error) < 15.0  # the rig size fixes the distance to camera


def test_foot_lock_removes_sliding():
    frames = list(range(1001, 1025))
    truth = ps.stand(frames)
    cams = {f: ps.static_camera(f, frames) for f in frames}
    model, data = ps.fit_frames(truth, cams, W, H, noise_px=3.0, seed=7)
    ankle = model.index["l_ankle"]

    def slide(lock):
        result = fit.RotoFitter(model, data, fit.FitOptions(foot_lock=lock, smooth_weight=0.0)).run()
        return float(np.mean(np.linalg.norm(np.diff(result.positions[:, ankle], axis=0), axis=1))), result

    free, _ = slide(False)
    locked, result = slide(True)
    assert sum(result.contacts["l"]) >= len(frames) // 2 and sum(result.contacts["r"]) >= len(frames) // 2
    assert locked < free * 0.5, (locked, free)


def test_fit_options_are_validated():
    assert fit.FitOptions.from_dict({"smooth_weight": 3, "foot_lock": False}).smooth_weight == 3.0
    assert fit.FitOptions.from_dict({"ground_height": None}).ground_height is None
    for bad in ({"smooth_weight": -1}, {"foot_lock": 1}, {"nope": 1}, {"up_axis": "x"}, {"max_iterations": 0}):
        with pytest.raises(ValueError):
            fit.FitOptions.from_dict(bad)


def test_rotation_helpers():
    r = np.array([0.3, -1.2, 0.5])
    m = fit.rodrigues(r)
    assert np.allclose(m @ m.T, np.eye(3)) and np.allclose(fit.rotvec(m), r)
    a = np.random.default_rng(0).normal(size=(5, 3))
    assert np.allclose(fit.kabsch(a, a @ m.T), m)
    assert np.allclose(fit.rodrigues(np.zeros(3)), np.eye(3))
    assert math.isclose(np.linalg.det(fit.body_frame([1, 0, 0], [-1, 0, 0], [0, 0, 0], [0, 1, 0])), 1.0)


# ------------------------------------------------------------------------------------ model archives
def test_zip_models_are_extracted_and_mirrors_used(tmp_path):
    from helpers import FileServer
    from ai_engine.models.model_manager import ModelManager, extracted_dir, find_file
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("rtm/end2end.onnx", b"onnx-bytes")
        zf.writestr("rtm/pipeline.json", "{}")
    data = archive.read_bytes()
    with FileServer({"m.zip": data}) as server:
        catalog = tmp_path / "catalog.json"
        catalog.write_text(json.dumps({"models": [{
            "model_id": "rtm", "name": "RTM", "task": "pose_2d", "backend": "rtmpose",
            "files": [{"name": "m.zip", "url": server.url("missing.zip"), "mirrors": [server.url("m.zip")],
                       "extract": True}]}]}))
        manager = ModelManager(tmp_path / "store", catalog)
        info = manager.install("rtm", lambda p, m: None, lambda: None)
    assert info.installed
    folder = tmp_path / "store" / "rtm" / extracted_dir("m.zip")
    assert find_file(folder, ".onnx").read_bytes() == b"onnx-bytes"


def test_zip_slip_is_refused(tmp_path):
    from ai_engine.models.model_manager import ModelError, extract_zip
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside.txt", "x")
    with pytest.raises(ModelError):
        extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists()


def test_catalog_has_commercial_friendly_pose_models():
    from ai_engine.models.model_manager import load_catalog
    catalog = load_catalog()
    for model_id in ("rtmpose_m_halpe26", "rtmpose_x_halpe26", "yolox_m_person", "mediapipe_pose_heavy"):
        info = catalog[model_id]
        assert "SMPL" not in info.license.replace("no SMPL", "")
        assert all(f.url.startswith("https://") for f in info.files)
    assert catalog["rtmpose_m_halpe26"].files[0].extract and catalog["rtmpose_m_halpe26"].files[0].mirrors
    assert catalog["mediapipe_pose_heavy"].files[0].sha256
