"""Plugin ConfigManager: layered defaults + user overrides."""
import json

from maya_plugin.core.config_manager import ConfigManager


def test_defaults_loaded():
    config = ConfigManager()
    assert config.get("engine.venv_dir") == ".venv_engine"
    assert config.get("engine.auto_connect_on_open") is True
    assert config.get("missing.key", "fallback") == "fallback"


def test_set_persists_only_overrides(tmp_path):
    user = tmp_path / "u.json"
    config = ConfigManager(user_path=user)
    config.set("engine.port", 9000)
    assert json.loads(user.read_text()) == {"engine": {"port": 9000}}
    assert ConfigManager(user_path=user).get("engine.port") == 9000
    assert ConfigManager(user_path=user).get("engine.venv_dir") == ".venv_engine"


def test_reset_restores_default(tmp_path):
    config = ConfigManager(user_path=tmp_path / "u.json")
    config.set("engine.port", 9000)
    config.reset("engine.port")
    assert config.get("engine.port") == 0


def test_corrupt_user_file_is_ignored(tmp_path):
    user = tmp_path / "u.json"
    user.write_text("{broken")
    assert ConfigManager(user_path=user).get("engine.venv_dir") == ".venv_engine"


def test_get_returns_copies(tmp_path):
    config = ConfigManager(user_path=tmp_path / "u.json")
    recent = config.get("projects.recent")
    recent.append("x")
    assert config.get("projects.recent") == []


def test_defaults_file_never_written(tmp_path):
    from helpers import ROOT
    defaults = ROOT / "config" / "default_settings.json"
    before = defaults.read_bytes()
    config = ConfigManager(user_path=tmp_path / "u.json")
    config.set("ui.default_module", "node_studio")
    assert defaults.read_bytes() == before


def test_save_failure_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    config = ConfigManager(user_path=blocker / "sub" / "u.json")
    config.set("engine.port", 1, save=False)
    assert config.save() is False
