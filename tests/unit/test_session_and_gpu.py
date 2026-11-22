"""Session file handling and GPU detection parsing."""
import pytest

from ai_engine.core.gpu_manager import GpuManager, parse_nvidia_smi_csv
from shared.contracts import ContractError
from shared.session import EngineSession, new_session, read_session_file, remove_session_file, write_session_file


def test_session_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "s" / "session.json"
    session = new_session(1234, "127.0.0.1", 50123, "k" * 40, "0.1.0", "v1")
    write_session_file(path, session)
    assert read_session_file(path) == session
    assert session.base_url == "http://127.0.0.1:50123"
    import os, sys
    if not sys.platform.startswith("win"):
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_missing_session_is_none(tmp_path):
    assert read_session_file(tmp_path / "none.json") is None


@pytest.mark.parametrize("content", ["{bad", "[]", '{"pid": 1}',
                                     '{"pid":1,"host":"0.0.0.0","port":5,"token":"x"}',
                                     '{"pid":1,"host":"127.0.0.1","port":0,"token":"x"}'])
def test_invalid_session_raises(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content)
    with pytest.raises(ContractError):
        read_session_file(path)


def test_remove_only_own_session(tmp_path):
    path = tmp_path / "s.json"
    write_session_file(path, EngineSession(10, "127.0.0.1", 5000, "x" * 40, "", "", 0.0))
    assert remove_session_file(path, expected_pid=11) is False
    assert path.exists()
    assert remove_session_file(path, expected_pid=10) is True
    assert not path.exists()


def test_parse_nvidia_smi():
    text = "0, NVIDIA GeForce RTX 4090, 24564, 551.23\n1, NVIDIA RTX A6000, 49140, 551.23\ngarbage\n"
    devices = parse_nvidia_smi_csv(text)
    assert [(d.index, d.name, d.memory_total_mb) for d in devices] == [
        (0, "NVIDIA GeForce RTX 4090", 24564), (1, "NVIDIA RTX A6000", 49140)]


def test_gpu_detection_without_driver(monkeypatch):
    import ai_engine.core.gpu_manager as gm
    monkeypatch.setattr(gm.shutil, "which", lambda _name: None)
    info = GpuManager({"detect_with_torch": False}).info
    assert not info.gpu_available and not info.cuda_available and info.method == "none"
