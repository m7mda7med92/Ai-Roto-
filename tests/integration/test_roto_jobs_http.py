"""Phase 02 engine API end to end over HTTP: models, proxies, segment, track (classical CPU backend)."""
import hashlib
import json

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from helpers import FileServer, InProcessEngine, iou, synthetic_plate, wait_job  # noqa: E402
from maya_plugin.communication.contracts import JobState, make_job_request  # noqa: E402
from maya_plugin.communication.engine_client import EngineApiError  # noqa: E402

PROMPTS = {"points": [[106, 90, 1]], "box": None}


def submit(client, job_type, params):
    return client.submit_job(make_job_request(job_type, params))


def test_model_routes_and_capabilities(engine):
    client = engine.client()
    models = {m.model_id: m for m in client.list_models()}
    assert models["opencv_grabcut"].installed and models["opencv_grabcut"].builtin
    assert models["sam2.1_hiera_tiny"].installed is False  # dotted ids route correctly
    assert client.get_model("sam2.1_hiera_large").backend == "sam2"
    with pytest.raises(EngineApiError) as err:
        client.get_model("does_not_exist")
    assert err.value.status == 404
    with pytest.raises(EngineApiError) as err:
        client.remove_model("opencv_grabcut")
    assert err.value.status == 409
    caps = client.capabilities()
    assert caps["modules"]["cv2"] is True and "roto_track" in caps["job_types"]


def test_install_and_remove_model_over_http(tmp_path):
    weights = b"w" * 300000
    with FileServer({"toy.pt": weights}) as server:
        catalog = tmp_path / "catalog.json"
        catalog.write_text(json.dumps({"models": [{
            "model_id": "toy", "name": "Toy", "task": "segmentation", "backend": "sam2", "version": "1",
            "files": [{"name": "toy.pt", "url": server.url("toy.pt"),
                       "sha256": hashlib.sha256(weights).hexdigest(), "size_bytes": len(weights)}]}]}))
        extra = {"models": {"catalog": str(catalog), "store_dir": str(tmp_path / "store")}}
        with InProcessEngine(tmp_path, extra) as running:
            client = running.client()
            status = wait_job(client, client.install_model("toy").job_id)
            assert status.state == JobState.COMPLETED, status.error
            assert status.result["model"]["installed"] and status.result["model"]["verified"]
            assert (tmp_path / "store" / "toy" / "toy.pt").read_bytes() == weights
            assert client.remove_model("toy").installed is False
            assert not (tmp_path / "store" / "toy").exists()


def test_proxies_segment_and_track(engine, tmp_path):
    client = engine.client()
    pattern, truth = synthetic_plate(tmp_path / "plate", count=6, step=8)
    proxies = tmp_path / "cache" / "proxies"

    status = wait_job(client, submit(client, "media_proxies", {
        "source": pattern, "output_dir": str(proxies), "max_size": 160}).job_id)
    assert status.state == JobState.COMPLETED, status.error
    assert status.result["frames"] == list(range(1001, 1007))
    assert status.result["proxy_size"] == [160, 90]
    assert status.result["media"]["width"] == 320
    assert (proxies / "proxy.1003.jpg").is_file()

    status = wait_job(client, submit(client, "segment_frame", {
        "source": pattern, "frame": 1001, "model_id": "opencv_grabcut", "prompts": PROMPTS,
        "preview_dir": str(tmp_path / "preview")}).job_id)
    assert status.state == JobState.COMPLETED, status.error
    preview = cv2.imread(status.result["mask_path"], cv2.IMREAD_UNCHANGED)
    assert preview.shape == (180, 320) and iou(preview > 127, truth[1001]) > 0.9

    out = tmp_path / "roto" / "hero"
    params = {"source": pattern, "model_id": "opencv_grabcut", "prompt_frame": 1003, "prompts":
              {"points": [[106 + 16, 90, 1]]}, "start": 1001, "end": 1006, "direction": "both",
              "refine": {"grow_px": 0, "feather_px": 0}, "output_dir": str(out), "name": "hero"}
    status = wait_job(client, submit(client, "roto_track", params).job_id)
    assert status.state == JobState.COMPLETED, status.error
    assert status.result["frames_written"] == 6 and status.result["frame_range"] == [1001, 1006]
    for frame, mask in truth.items():
        matte = cv2.imread(str(out / ("hero.%04d.png" % frame)), cv2.IMREAD_UNCHANGED)
        assert iou(matte > 127, mask) > 0.85, frame
    manifest = json.loads((out / "hero.roto.json").read_text())
    assert manifest["prompt_frame"] == 1003 and manifest["pattern"] == "hero.####.png"
    assert len(manifest["frames"]) == 6

    # Existing mattes are never overwritten without explicit permission.
    before = (out / "hero.1001.png").stat().st_mtime_ns
    status = wait_job(client, submit(client, "roto_track", params).job_id)
    assert status.state == JobState.FAILED and "already exist" in status.error.message
    assert (out / "hero.1001.png").stat().st_mtime_ns == before
    status = wait_job(client, submit(client, "roto_track", dict(params, overwrite=True, direction="forward",
                                                                 start=1003)).job_id)
    assert status.state == JobState.COMPLETED and status.result["frames_written"] == 4


def test_track_can_be_cancelled(engine, tmp_path):
    client = engine.client()
    pattern, _ = synthetic_plate(tmp_path / "plate", count=40, step=2, width=640, height=360)
    job = submit(client, "roto_track", {
        "source": pattern, "model_id": "opencv_grabcut", "prompt_frame": 1001, "prompts":
        {"points": [[213, 180, 1]]}, "direction": "forward", "output_dir": str(tmp_path / "out"), "name": "c"})
    from helpers import wait_until
    assert wait_until(lambda: client.get_job(job.job_id).progress > 0.1, 30)
    client.cancel_job(job.job_id)
    status = wait_job(client, job.job_id)
    assert status.state == JobState.CANCELLED
    written = len(list((tmp_path / "out").glob("c.*.png")))
    assert 0 < written < 40 and not (tmp_path / "out" / "c.roto.json").exists()


@pytest.mark.parametrize("job_type,params,field", [
    ("media_proxies", {"source": "relative/plate.####.png", "output_dir": "/tmp/x"}, "source"),
    ("segment_frame", {"source": "PLATE", "frame": 1001, "model_id": "sam2.1_hiera_tiny",
                       "prompts": PROMPTS, "preview_dir": "/tmp/p"}, "model_id"),
    ("segment_frame", {"source": "PLATE", "frame": 1001, "model_id": "opencv_grabcut",
                       "prompts": {"points": []}, "preview_dir": "/tmp/p"}, "prompts"),
    ("roto_track", {"source": "PLATE", "model_id": "opencv_grabcut", "prompt_frame": 1001, "prompts": PROMPTS,
                    "start": 1002, "end": 1005, "output_dir": "/tmp/o"}, "start"),
    ("roto_track", {"source": "PLATE", "model_id": "opencv_grabcut", "prompt_frame": 1001, "prompts": PROMPTS,
                    "output_dir": "/tmp/o", "name": "../evil"}, "name"),
    ("model_install", {"model_id": "opencv_grabcut"}, "model_id"),
    ("roto_track", {"source": "PLATE", "model_id": "opencv_grabcut", "prompt_frame": 1001, "prompts": PROMPTS,
                    "output_dir": "/tmp/o", "guides": {"abc": {"points": [[1, 1, 1]]}}}, "guides"),
    ("roto_track", {"source": "PLATE", "model_id": "opencv_grabcut", "prompt_frame": 1001,
                    "output_dir": "/tmp/o", "guides": {"1002": {"points": [[1, 1, 1]]}}}, "prompts"),
    ("roto_track", {"source": "PLATE", "model_id": "opencv_grabcut", "prompt_frame": 1001, "prompts": PROMPTS,
                    "output_dir": "/tmp/o", "guide_interval": 0}, "guide_interval"),
])
def test_validation_errors(engine, tmp_path, job_type, params, field):
    pattern, _ = synthetic_plate(tmp_path / "plate", count=2)
    params = {k: (pattern if v == "PLATE" else v) for k, v in params.items()}
    with pytest.raises(EngineApiError) as err:
        submit(engine.client(), job_type, params)
    assert err.value.status == 422 and err.value.details.get("field") == field


def test_maya_guided_track_without_clicks(engine, tmp_path):
    """Phase 03: guides projected from Maya tracking drive the whole track (no manual prompts)."""
    from helpers import jump_guides, jump_plate
    client = engine.client()
    pattern, truth = jump_plate(tmp_path / "plate")
    guides = jump_guides()
    guides["1004"]["points"].append([2000, 90, 1])  # off-frame points are dropped, not an error
    out = tmp_path / "roto" / "guided"
    status = wait_job(client, submit(client, "roto_track", {
        "source": pattern, "model_id": "opencv_grabcut", "prompt_frame": 1001, "direction": "forward",
        "output_dir": str(out), "name": "guided", "guides": guides, "guide_interval": 1,
        "guide_source": "maya camera cam1"}).job_id)
    assert status.state == JobState.COMPLETED, status.error
    assert status.result["guided_frames"] == 6 and status.result["drift_frames"] == []
    for frame, mask in truth.items():
        assert iou(cv2.imread(str(out / ("guided.%04d.png" % frame)), cv2.IMREAD_UNCHANGED) > 127, mask) > 0.85
    manifest = json.loads((out / "guided.roto.json").read_text())
    assert manifest["guides"] == {"source": "maya camera cam1", "frames": 6, "used": 6, "interval": 1}
    assert manifest["frames"]["1004"]["guide_points_inside"] == 1.0
    assert manifest["frames"]["1004"]["guide_box_iou"] > 0.8 and not manifest["frames"]["1004"]["drift"]

    # Guides every 2nd frame are still used for the drift report on every guided frame.
    status = wait_job(client, submit(client, "roto_track", {
        "source": pattern, "model_id": "opencv_grabcut", "prompt_frame": 1001, "direction": "forward",
        "output_dir": str(out), "name": "sparse", "guides": guides, "guide_interval": 2}).job_id)
    assert status.state == JobState.COMPLETED, status.error
    assert status.result["guided_frames"] == 3
    manifest = json.loads((out / "sparse.roto.json").read_text())
    assert manifest["guides"]["used"] == 3 and all("drift" in manifest["frames"][str(f)] for f in truth)
