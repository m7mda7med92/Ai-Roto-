"""Shared fixtures. Every test gets an isolated per-user data folder (MAIT_USER_DIR)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import InProcessEngine  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user_dir = tmp_path / "user"
    monkeypatch.setenv("MAIT_USER_DIR", str(user_dir))
    monkeypatch.delenv("MAIT_SESSION_FILE", raising=False)
    monkeypatch.delenv("MAIT_ENGINE_TOKEN", raising=False)
    return user_dir


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[InProcessEngine]:
    with InProcessEngine(tmp_path) as running:
        yield running


@pytest.fixture
def plugin_config(tmp_path: Path):
    """A ConfigManager whose engine Python is the interpreter running the tests."""
    from maya_plugin.core.config_manager import ConfigManager
    config = ConfigManager(user_path=tmp_path / "user_settings.json")
    config.set("engine.python_executable", sys.executable, save=False)
    config.set("engine.health_check_interval_seconds", 0.5, save=False)
    config.set("jobs.poll_interval_seconds", 0.1, save=False)
    config.set("engine.startup_timeout_seconds", 30, save=False)
    return config
