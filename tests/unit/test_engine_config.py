"""Engine configuration loading and validation."""
import json

import pytest

from ai_engine.utils.config import ConfigError, DEFAULT_ENGINE_SETTINGS, load_engine_settings


def test_shipped_settings_file_is_valid(tmp_path):
    from helpers import ROOT
    settings = load_engine_settings(ROOT / "config" / "engine_settings.json")
    assert settings["server"]["host"] == "127.0.0.1"


def test_missing_default_file_uses_defaults(monkeypatch, tmp_path):
    import ai_engine.utils.config as cfg
    monkeypatch.setattr(cfg, "default_config_path", lambda: tmp_path / "nope.json")
    assert load_engine_settings() == DEFAULT_ENGINE_SETTINGS


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_engine_settings(tmp_path / "nope.json")


def test_file_values_merge_over_defaults(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"jobs": {"max_concurrent_jobs": 4}}))
    settings = load_engine_settings(path)
    assert settings["jobs"]["max_concurrent_jobs"] == 4
    assert settings["jobs"]["history_limit"] == DEFAULT_ENGINE_SETTINGS["jobs"]["history_limit"]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "::"])
def test_non_loopback_host_rejected(tmp_path, host):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"server": {"host": host}}))
    with pytest.raises(ConfigError):
        load_engine_settings(path)


@pytest.mark.parametrize("overrides", [{"server": {"port": 70000}}, {"server": {"port": "80"}},
                                       {"jobs": {"max_concurrent_jobs": 0}}])
def test_invalid_values_rejected(overrides):
    with pytest.raises(ConfigError):
        load_engine_settings(overrides=overrides)


def test_invalid_json_rejected(tmp_path):
    path = tmp_path / "e.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError):
        load_engine_settings(path)
