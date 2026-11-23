"""Optional: the pose_track job with the REAL MediaPipe Pose Landmarker on a real photo.

Skipped unless ``MAIT_TEST_MEDIAPIPE_TASK`` (a pose_landmarker_*.task file) and
``MAIT_TEST_PERSON_IMAGE`` (a photo of one person) are set and ``mediapipe`` is installed.
"""
import json
import os
import shutil

import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("mediapipe")
TASK = os.environ.get("MAIT_TEST_MEDIAPIPE_TASK", "")
IMAGE = os.environ.get("MAIT_TEST_PERSON_IMAGE", "")
pytestmark = pytest.mark.skipif(not (os.path.isfile(TASK) and os.path.isfile(IMAGE)),
                                reason="set MAIT_TEST_MEDIAPIPE_TASK and MAIT_TEST_PERSON_IMAGE")

from helpers import InProcessEngine, wait_job  # noqa: E402
from maya_plugin.communication.contracts import JobState  # noqa: E402
from maya_plugin.modules.body_tracking import roto_requests as rq  # noqa: E402


def test_mediapipe_pose_track_on_a_photo(tmp_path):
    from ai_engine.pose import skeleton as sk
    from ai_engine.pose.backends import MediaPipeBackend
    folder = tmp_path / "plate"
    folder.mkdir()
    for f in (1001, 1002, 1003):
        shutil.copy(IMAGE, folder / ("photo.%04d.jpg" % f))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": [{"model_id": "mp", "name": "MediaPipe", "task": "body_3d",
                                               "backend": "mediapipe", "builtin": True,
                                               "options": {"also": ["pose_2d"]}}]}))
    with InProcessEngine(tmp_path, {"models": {"catalog": str(catalog), "store_dir": str(tmp_path / "s")}}) as engine:
        engine.manager.poses.register("mp", MediaPipeBackend(TASK))
        client = engine.client()
        request = rq.pose_request(str(folder / "photo.####.jpg"), 1001, "mp", str(tmp_path / "out"), "photo",
                                  body3d_model_id="mp")
        status = wait_job(client, client.submit_job(request).job_id, 120)
        assert status.state == JobState.COMPLETED, status.error
    pose = json.loads((tmp_path / "out" / "photo.pose.json").read_text())
    height, width = cv2.imread(IMAGE).shape[:2]
    for frame in ("1001", "1002", "1003"):
        entry = pose["frames"][frame]
        confident = [p for p in entry["kp2d"] if p[2] > 0.5]
        assert len(confident) >= 12
        assert all(0 <= p[0] < width and 0 <= p[1] < height for p in confident)
        assert entry["kp3d"] is not None
        body = entry["kp3d"]
        # Maya camera space: the head is above the pelvis (y up)
        assert body[sk.INDEX["head"]][1] > body[sk.INDEX["pelvis"]][1]
    assert status.result["mean_confidence"] > 0.5
