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


def fast_engine_settings(extra: dict = None, **jobs: int) -> dict:
    from ai_engine.utils.config import deep_merge, load_engine_settings
    overrides = {"server": {"port": 0}, "logging": {"level": "WARNING", "json_file": False},
                 "gpu": {"detect_with_torch": False}, "jobs": jobs}
    return load_engine_settings(ROOT / "config" / "engine_settings.json", deep_merge(overrides, extra or {}))


class InProcessEngine:
    """Runs an EngineManager on a background thread (fast HTTP-level tests)."""

    def __init__(self, tmp: Path, extra: dict = None, **jobs: int) -> None:
        from ai_engine.core.engine_manager import EngineManager
        self.session_file = tmp / "session.json"
        self.manager = EngineManager(fast_engine_settings(extra, **jobs), TEST_TOKEN, session_file=self.session_file)
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


def wait_job(client, job_id: str, timeout: float = 60.0):
    """Poll an engine job until it reaches a terminal state."""
    deadline = time.time() + timeout
    status = client.get_job(job_id)
    while not status.is_terminal and time.time() < deadline:
        time.sleep(0.05)
        status = client.get_job(job_id)
    return status


def synthetic_plate(folder: Path, count: int = 8, first: int = 1001, width: int = 320, height: int = 180,
                    step: int = 6, ext: str = "png"):
    """Write a numbered sequence of a red disc moving right over a textured green background.

    Returns ``(pattern, ground_truth)`` where ground_truth maps frame -> bool mask.
    """
    import cv2
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    texture = rng.integers(0, 50, (height, width, 3)).astype(np.uint8)
    truth = {}
    radius = max(8, height // 6)
    for index in range(count):
        frame = first + index
        rgb = (texture * 0.6 + np.array([40, 90, 50])).astype(np.uint8)
        center = (width // 3 + index * step, height // 2)
        cv2.circle(rgb, center, radius, (230, 60, 50), -1)
        mask = np.zeros((height, width), np.uint8)
        cv2.circle(mask, center, radius, 1, -1)
        truth[frame] = mask.astype(bool)
        cv2.imwrite(str(folder / ("plate.%04d.%s" % (frame, ext))), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return str(folder / ("plate.####.%s" % ext)), truth


def iou(a, b) -> float:
    return float((a & b).sum()) / max(1, int((a | b).sum()))


class FileServer:
    """Loopback HTTP server serving in-memory files (model download tests)."""

    def __init__(self, files: dict, chunk_delay: float = 0.0) -> None:
        import http.server
        files_ref, delay = files, chunk_delay

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                data = files_ref.get(self.path.lstrip("/"))
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                step = 64 * 1024
                try:
                    for offset in range(0, len(data), step):
                        self.wfile.write(data[offset:offset + step])
                        if delay:
                            time.sleep(delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def url(self, name: str) -> str:
        return "http://127.0.0.1:%d/%s" % (self.server.server_address[1], name)

    def __enter__(self) -> "FileServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


# ------------------------------------------------------------------ Phase 03 (Maya-guided roto)
JUMP_WIDTH, JUMP_HEIGHT, JUMP_STEP = 480, 180, 45


def jump_center(index: int):
    """Disc centre in the fast-moving plate (too fast for optical flow alone)."""
    return JUMP_WIDTH // 3 + index * JUMP_STEP, JUMP_HEIGHT // 2


def jump_plate(folder: Path, count: int = 6):
    """Red disc jumping 45 px per frame: unguided tracking loses it, Maya guides recover it."""
    return synthetic_plate(folder, count=count, width=JUMP_WIDTH, height=JUMP_HEIGHT, step=JUMP_STEP)


def jump_guides(count: int = 6, first: int = 1001, points: bool = True, box: bool = True) -> dict:
    """What projected Maya tracking would give for :func:`jump_plate` (string frame keys)."""
    radius = max(8, JUMP_HEIGHT // 6)
    out = {}
    for index in range(count):
        x, y = jump_center(index)
        out[str(first + index)] = {"points": [[x, y, 1]] if points else [],
                                   "box": [x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3]
                                   if box else None}
    return out
