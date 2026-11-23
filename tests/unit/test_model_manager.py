"""Model store: catalog validation, download/verify/install, cancel, remove (loopback HTTP only)."""
import hashlib
import json
import threading

import pytest

from ai_engine.models.model_manager import ModelBusy, ModelError, ModelManager, UnknownModel, load_catalog
from helpers import FileServer, ROOT

WEIGHTS = bytes(range(256)) * 4096  # 1 MiB


def write_catalog(tmp_path, url, sha256=None, size=None, extra=None):
    entry = {"model_id": "toy_model", "name": "Toy Model", "task": "segmentation", "backend": "sam2",
             "version": "1", "files": [{"name": "toy.pt", "url": url, "sha256": sha256, "size_bytes": size}],
             "options": {"config": "x.yaml"}}
    builtin = {"model_id": "builtin_one", "name": "Builtin", "task": "segmentation", "backend": "classical",
               "version": "1", "builtin": True, "files": []}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"models": [entry, builtin] + (extra or [])}), encoding="utf-8")
    return path


def no_cancel():
    return None


def test_shipped_catalog_is_valid():
    catalog = load_catalog(ROOT / "config" / "model_catalog.json")
    assert "opencv_grabcut" in catalog and catalog["opencv_grabcut"].builtin
    sam = [m for m in catalog.values() if m.backend == "sam2"]
    assert len(sam) == 4
    for model in sam:
        assert model.files[0].url.startswith("https://dl.fbaipublicfiles.com/segment_anything_2/")
        assert model.options["config"].startswith("configs/sam2.1/")


def test_catalog_rejects_plain_http_to_remote_hosts(tmp_path):
    path = write_catalog(tmp_path, "http://example.com/toy.pt")
    with pytest.raises(ModelError, match="https"):
        load_catalog(path)


def test_install_verifies_checksum_and_marks_installed(tmp_path):
    sha = hashlib.sha256(WEIGHTS).hexdigest()
    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt"), sha, len(WEIGHTS)))
        assert manager.get("toy_model").installed is False
        progress = []
        info = manager.install("toy_model", lambda p, m: progress.append(p), no_cancel)
    assert info.installed and info.verified is True
    assert manager.file_path("toy_model").read_bytes() == WEIGHTS
    assert progress[-1] == 1.0 and all(0 <= p <= 1 for p in progress)
    assert not list((tmp_path / "store" / "toy_model").glob("*.part"))


def test_checksum_mismatch_leaves_model_uninstalled(tmp_path):
    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt"), "0" * 64))
        with pytest.raises(ModelError, match="checksum"):
            manager.install("toy_model", lambda p, m: None, no_cancel)
    assert manager.get("toy_model").installed is False
    assert not (tmp_path / "store" / "toy_model" / "toy.pt").exists()


def test_size_mismatch_and_http_error(tmp_path):
    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt"), size=123))
        with pytest.raises(ModelError, match="size mismatch"):
            manager.install("toy_model", lambda p, m: None, no_cancel)
        manager = ModelManager(tmp_path / "store2", write_catalog(tmp_path, server.url("missing.pt")))
        with pytest.raises(ModelError, match="download failed"):
            manager.install("toy_model", lambda p, m: None, no_cancel)
    assert manager.get("toy_model").installed is False


def test_unverified_install_is_flagged(tmp_path):
    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt")))
        info = manager.install("toy_model", lambda p, m: None, no_cancel)
    assert info.installed and info.verified is False


class Cancelled(Exception):
    pass


def test_cancel_mid_download_removes_partial_file(tmp_path):
    big = WEIGHTS * 4
    calls = {"n": 0}

    def checkpoint():
        calls["n"] += 1
        if calls["n"] > 2:
            raise Cancelled()

    with FileServer({"toy.pt": big}, chunk_delay=0.001) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt")))
        with pytest.raises(Cancelled):
            manager.install("toy_model", lambda p, m: None, checkpoint)
    folder = tmp_path / "store" / "toy_model"
    assert not list(folder.glob("*")) or not (folder / "installed.json").exists()
    assert not list(folder.glob("*.part"))
    assert manager.get("toy_model").installed is False


def test_remove_and_builtin_rules(tmp_path):
    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt")))
        manager.install("toy_model", lambda p, m: None, no_cancel)
    assert manager.remove("toy_model").installed is False
    assert not (tmp_path / "store" / "toy_model").exists()
    assert manager.get("builtin_one").installed is True
    with pytest.raises(ModelError, match="built-in"):
        manager.remove("builtin_one")
    with pytest.raises(UnknownModel):
        manager.get("nope")


def test_concurrent_install_is_refused(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow_checkpoint():
        started.set()
        release.wait(5)

    with FileServer({"toy.pt": WEIGHTS}) as server:
        manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, server.url("toy.pt")))
        worker = threading.Thread(target=lambda: manager.install("toy_model", lambda p, m: None, slow_checkpoint))
        worker.start()
        assert started.wait(5)
        with pytest.raises(ModelBusy):
            manager.install("toy_model", lambda p, m: None, no_cancel)
        release.set()
        worker.join(10)
    assert manager.get("toy_model").installed


def test_model_folder_cannot_escape_store(tmp_path):
    manager = ModelManager(tmp_path / "store", write_catalog(tmp_path, "https://example.com/toy.pt"))
    with pytest.raises(ModelError):
        manager._folder("../outside")
