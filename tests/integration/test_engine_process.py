"""The engine as a real, independent OS process (``python -m ai_engine.main``)."""
import os
import signal
import subprocess
import sys

import pytest

from helpers import ROOT, wait_until
from maya_plugin.communication.connection_manager import build_engine_env
from maya_plugin.communication.engine_client import EngineClient
from shared.session import read_session_file

pytestmark = pytest.mark.process
TOKEN = "p" * 48


def spawn(tmp_path, *extra, token=TOKEN):
    session = tmp_path / "session.json"
    env = build_engine_env(os.environ, ROOT, token)
    proc = subprocess.Popen([sys.executable, "-m", "ai_engine.main", "--session-file", str(session),
                             "--log-dir", str(tmp_path / "logs"), "--port", "0", *extra],
                            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc, session


def test_process_lifecycle_and_graceful_signal(tmp_path):
    proc, session_path = spawn(tmp_path)
    try:
        assert wait_until(lambda: session_path.exists(), timeout=20)
        session = read_session_file(session_path)
        assert session.pid == proc.pid and session.host == "127.0.0.1" and session.token == TOKEN
        client = EngineClient(session.host, session.port, session.token)
        assert client.health().pid == proc.pid
        assert (tmp_path / "logs" / "engine.jsonl").exists()
        if sys.platform.startswith("win"):
            client.shutdown()
        else:
            proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
        assert not session_path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_token_is_not_on_command_line(tmp_path):
    proc, session_path = spawn(tmp_path)
    try:
        assert wait_until(lambda: session_path.exists(), timeout=20)
        if os.path.exists("/proc/%d/cmdline" % proc.pid):
            cmdline = open("/proc/%d/cmdline" % proc.pid, "rb").read().decode()
            assert TOKEN not in cmdline
    finally:
        proc.kill()
        proc.wait()


def test_refuses_public_bind(tmp_path):
    proc, _ = spawn(tmp_path, "--host", "0.0.0.0")
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 2 and b"127.0.0.1" in out


def test_refuses_weak_token(tmp_path):
    proc, _ = spawn(tmp_path, token="short")
    proc.communicate(timeout=20)
    assert proc.returncode == 2


def test_port_in_use_fails_cleanly(tmp_path):
    import socket
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        proc, _ = spawn(tmp_path, "--port", str(blocker.getsockname()[1]))
        proc.communicate(timeout=20)
        assert proc.returncode == 3
    finally:
        blocker.close()


def test_parent_watchdog_stops_orphaned_engine(tmp_path):
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    proc, session_path = spawn(tmp_path, "--parent-pid", str(parent.pid))
    try:
        assert wait_until(lambda: session_path.exists(), timeout=20)
        parent.kill()
        parent.wait()
        assert proc.wait(timeout=20) == 0
        assert not session_path.exists()
    finally:
        for p in (parent, proc):
            if p.poll() is None:
                p.kill()
