"""Test helpers shared by unit, integration and mock-Maya tests."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_TOKEN = "t" * 48


def fast_engine_settings(**jobs: int) -> dict:
    from ai_engine.utils.config import load_engine_settings
    overrides = {"server": {"port": 0}, "logging": {"level": "WARNING", "json_file": False},
                 "gpu": {"detect_with_torch": False}, "jobs": jobs}
    return load_engine_settings(ROOT / "config" / "engine_settings.json", overrides)


class InProcessEngine:
    """Runs an EngineManager on a background thread (fast HTTP-level tests)."""

    def __init__(self, tmp: Path, **jobs: int) -> None:
        from ai_engine.core.engine_manager import EngineManager
        self.session_file = tmp / "session.json"
        self.manager = EngineManager(fast_engine_settings(**jobs), TEST_TOKEN, session_file=self.session_file)
        self.thread = threading.Thread(target=self.manager.serve_forever, daemon=True)

    def __enter__(self) -> "InProcessEngine":
        self.thread.start()
        deadline = time.time() + 10
        while not self.session_file.exists() and time.time() < deadline:
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self.manager.request_shutdown("test")
        self.thread.join(timeout=15)

    @property
    def port(self) -> int:
        return self.manager.port

    def client(self, token: str = TEST_TOKEN):
        from maya_plugin.communication.engine_client import EngineClient
        return EngineClient("127.0.0.1", self.port, token, timeout=5)


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
