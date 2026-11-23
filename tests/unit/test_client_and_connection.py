"""Engine client error handling and connection-manager behaviour without a running engine."""
import socket
import sys
from pathlib import Path

import pytest

from maya_plugin.communication.connection_manager import ConnectionManager, build_engine_env, is_maya_interpreter
from maya_plugin.communication.engine_client import (EngineClient, EngineConnectionError, EngineResponseError,
                                                     EngineTimeoutError)
from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.event_manager import EventManager, Events
from shared.constants.engine_constants import ConnectionState


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_client_refuses_non_loopback():
    with pytest.raises(ValueError):
        EngineClient("10.0.0.5", 80, "x")


def test_connection_refused_is_structured():
    client = EngineClient("127.0.0.1", free_port(), "x" * 40, timeout=2)
    with pytest.raises(EngineConnectionError) as info:
        client.health()
    assert info.value.code == "connection_failed"


def test_timeout_is_structured():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)  # accepts the TCP connection but never answers
    try:
        client = EngineClient("127.0.0.1", server.getsockname()[1], "x" * 40, timeout=0.5)
        with pytest.raises((EngineTimeoutError, EngineConnectionError)):
            client.health()
    finally:
        server.close()


def test_invalid_response_is_structured():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"not json!")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(EngineResponseError):
            EngineClient("127.0.0.1", httpd.server_address[1], "x" * 40).health()
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("name, expected", [("maya.exe", True), ("mayapy.exe", True), ("mayapy", True),
                                            ("Maya.EXE", True), ("mayabatch.exe", True), ("python.exe", False),
                                            ("python3", False), ("mayavi", False)])
def test_maya_interpreter_detection(name, expected):
    assert is_maya_interpreter(Path(name)) is expected


def test_engine_env_scrubs_maya_python_variables(tmp_path):
    env = build_engine_env({"PYTHONHOME": "C:/Maya/Python", "PYTHONPATH": "C:/Maya/site", "PATH": "x",
                            "pythonstartup": "y"}, tmp_path, "tok")
    assert "PYTHONHOME" not in env and "pythonstartup" not in env
    assert env["PYTHONPATH"] == str(tmp_path) and env["MAIT_ENGINE_TOKEN"] == "tok" and env["PATH"] == "x"


def test_not_configured_when_no_engine_python(tmp_path):
    config = ConfigManager(user_path=tmp_path / "u.json")
    config.set("engine.venv_dir", str(tmp_path / "missing_venv"), save=False)
    manager = ConnectionManager(config, EventManager(), root=tmp_path)
    try:
        assert manager.state == ConnectionState.NOT_CONFIGURED
        assert manager.start_engine().result(5) is False
        assert manager.state == ConnectionState.NOT_CONFIGURED
    finally:
        manager.shutdown()


def test_maya_python_is_rejected_as_engine(tmp_path):
    fake = tmp_path / "mayapy.exe"
    fake.write_text("")
    config = ConfigManager(user_path=tmp_path / "u.json")
    config.set("engine.python_executable", str(fake), save=False)
    manager = ConnectionManager(config, EventManager(), root=tmp_path)
    try:
        assert manager.resolve_engine_python() is None
        assert manager.state == ConnectionState.NOT_CONFIGURED
    finally:
        manager.shutdown()


def test_venv_autodetection(tmp_path):
    venv_python = tmp_path / ".venv_engine" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    manager = ConnectionManager(ConfigManager(user_path=tmp_path / "u.json"), EventManager(), root=tmp_path)
    try:
        assert manager.resolve_engine_python() == venv_python
        assert manager.state == ConnectionState.OFFLINE
    finally:
        manager.shutdown()


def test_connect_without_running_engine_goes_offline_without_raising(plugin_config):
    events = EventManager()
    states = []
    events.subscribe(Events.CONNECTION_STATE_CHANGED, lambda s: states.append(s.state))
    manager = ConnectionManager(plugin_config, events)
    try:
        assert manager.connect().result(5) is False
        assert manager.state == ConnectionState.OFFLINE
        assert manager.client is None
        assert manager.health_check().result(5) is None
    finally:
        manager.shutdown()


def test_stale_session_file_is_cleaned(plugin_config):
    from shared.paths import session_file
    from shared.session import new_session, write_session_file
    write_session_file(session_file(), new_session(999999, "127.0.0.1", 5555, "x" * 40, "0", "v1"))
    manager = ConnectionManager(plugin_config, EventManager())
    try:
        assert manager.connect().result(5) is False
        assert not session_file().exists()
    finally:
        manager.shutdown()


def test_operations_after_shutdown_are_noops(plugin_config):
    manager = ConnectionManager(plugin_config, EventManager())
    manager.shutdown()
    assert manager.start_engine().result(1) is False
