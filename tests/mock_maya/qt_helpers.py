"""Helpers for MOCK MAYA tests (event pumping for offscreen Qt)."""
from __future__ import annotations

import time
from pathlib import Path

MOCKS = Path(__file__).resolve().parent.parent / "mocks"


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def pump_until(app, predicate, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
