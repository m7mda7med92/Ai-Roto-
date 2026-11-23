"""Phase 04 engine jobs over HTTP: pose_track (with a FAKE pose model) then roto_fit on a synthetic shot.

The fake model reads the frame number painted into the plate and returns the synthetic body for that
frame, so this exercises the job plumbing, subject following, files and the fit end to end. Real
RTMPose / MediaPipe models are not used here (see docs/MAYA_2024_MANUAL_TEST.md section I).
"""
import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import pose_synthetic as ps  # noqa: E402
from helpers import InProcessEngine, wait_job  # noqa: E402
from maya_plugin.communication.contracts import JobState, make_job_request  # noqa: E402
from maya_plugin.communication.engine_client import EngineApiError  # noqa: E402
from maya_plugin.modules.body_tracking import roto_requests as rq  # noqa: E402
from maya_plugin.modules.roto_studio import maya_guides as mg  # noqa: E402

W, H = 640, 360
FRAMES = list(range(1001, 1009))


class FakePose:
    name = "fake"

    def __init__(self, obs):
        self.obs = obs
        self.boxes = []

    def _frame(self, image):
        return 1000 + int(image[0, 0, 0])

    def pose_2d(self, image, box):
        self.boxes.append(list(box))
        return self.obs[self._frame(image)]["kp2d"]

    def body_3d(self, image, box):
        return self.obs[self._frame(image)]["kp3d"]


def plate(folder):
    folder.mkdir(parents=True)
    for f in FRAMES:
        img = np.full((H, W, 3), 40, np.uint8)
        img[0, 0] = f - 1000
        cv2.imwrite(str(folder / ("shot.%04d.png" % f)), img)
    return str(folder / "shot.####.png")


@pytest.fixture
def pose_engine(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": [
        {"model_id": "fake_pose", "name": "Fake pose", "task": "pose_2d", "backend": "fake", "builtin": True,
         "options": {"also": ["body_3d"]}},
        {"model_id": "fake_seg", "name": "Fake seg", "task": "segmentation", "backend": "classical", "builtin": True}]}))
    with InProcessEngine(tmp_path, {"models": {"catalog": str(catalog), "store_dir": str(tmp_path / "store")}}) as e:
        yield e


def test_pose_track_and_fit_over_http(pose_engine, tmp_path):
    truth = ps.walk(FRAMES)
    cams = {f: ps.camera_sample(f, FRAMES) for f in FRAMES}
    obs = ps.observations(truth, cams, W, H, noise_px=0.5)
    fake = FakePose(obs)
    pose_engine.manager.poses.register("fake_pose", fake)
    client = pose_engine.client()
    source = plate(tmp_path / "plate")
    out = tmp_path / "rotomation" / "hero"
    request = rq.pose_request(source, 1001, "fake_pose", str(out), "hero", body3d_model_id="fake_pose",
                              subject={"frame": 1004, "box": [100, 20, 300, 340]}, start=1001, end=1008)
    status = wait_job(client, client.submit_job(request).job_id, 60)
    assert status.state == JobState.COMPLETED, status.error
    pose = json.loads((out / "hero.pose.json").read_text())
    assert status.result["frames"] == 8 and status.result["has_3d"] and pose["kind"] == "pose_track"
    assert pose["frames"]["1004"]["box_source"] == "artist" and pose["frames"]["1005"]["box_source"] == "previous"
    assert fake.boxes[0] == [100.0, 20.0, 300.0, 340.0]  # starts on the artist's box, then follows the body

    # the same file again is refused unless overwrite is set
    again = wait_job(client, client.submit_job(request).job_id, 30)
    assert again.state == JobState.FAILED and "already exists" in again.error.message

    samples = {f: mg.CameraSample(**cams[f]) for f in FRAMES}
    fit_input = rq.write_json(rq.fit_input_file(str(out), "hero"),
                              rq.build_fit_input("|shotCam", samples, "hero_rig", 1001, ps.REST, "y", 0))
    fit_req = rq.fit_request(str(out / "hero.pose.json"), str(fit_input), str(out), "hero", {"smooth_weight": 4.0})
    status = wait_job(client, client.submit_job(fit_req).job_id, 120)
    assert status.state == JobState.COMPLETED, status.error
    assert status.result["mean_error_px"] < 2.0 and status.result["frames"] == 8
    fit = json.loads((out / "hero.rotofit.json").read_text())
    joints = fit["joints"]
    errors = [np.linalg.norm(np.array(fit["frames"][str(f)]["p"][joints.index("l_wrist")]) - truth[f]["l_wrist"])
              for f in FRAMES]
    assert max(errors) < 6.0, errors


def test_pose_jobs_validate_params(pose_engine, tmp_path):
    client = pose_engine.client()
    source = plate(tmp_path / "plate")
    bad = [
        ("pose_track", {"source": source, "model_id": "fake_seg", "output_dir": str(tmp_path)}),   # wrong task
        ("pose_track", {"source": source, "model_id": "nope", "output_dir": str(tmp_path)}),
        ("pose_track", {"source": source, "model_id": "fake_pose", "output_dir": "relative"}),
        ("pose_track", {"source": source, "model_id": "fake_pose", "output_dir": str(tmp_path),
                        "subject": {"frame": 1001, "box": [0, 0, 1, 1]}}),
        ("roto_fit", {"pose_file": str(tmp_path / "missing.json"), "fit_input": source, "output_dir": str(tmp_path)}),
        ("roto_fit", {"pose_file": source, "fit_input": source, "output_dir": str(tmp_path),
                      "options": {"smooth_weight": "high"}}),
    ]
    for job_type, params in bad:
        with pytest.raises(EngineApiError) as err:
            client.submit_job(make_job_request(job_type, params))
        assert err.value.status == 422, (job_type, params)
