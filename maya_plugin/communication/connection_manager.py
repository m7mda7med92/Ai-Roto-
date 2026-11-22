"""Engine connection manager: start / stop / restart / connect / disconnect / health.

Design rules
------------
* Never blocks the caller. Every public operation is queued on a dedicated
  single-thread executor (operations are serialised) and returns a ``Future``.
* Never raises into the UI. Failures become state changes plus
  ``Events.ENGINE_MESSAGE`` events.
* Never uses Maya's interpreter for the engine. The engine Python is resolved
  from config or the toolkit's engine venv; ``maya``/``mayapy`` executables are
  rejected explicitly.
* Qt-free and Maya-free, so it is fully unit/integration testable.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, IO, List, Optional

from maya_plugin.communication.engine_client import EngineClient, EngineError
from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.logger import get_logger
from shared.constants.engine_constants import ENV_ENGINE_TOKEN, ConnectionState
from shared.contracts.errors import ContractError
from shared.contracts.job_contracts import HealthInfo
from shared.paths import logs_dir, session_file, toolkit_root
from shared.process_utils import pid_exists
from shared.session import EngineSession, read_session_file, remove_session_file

log = get_logger("connection")

# Environment variables that would make the engine's Python load Maya's stdlib/site-packages.
_SCRUBBED_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE", "PYTHONUSERBASE",
                 "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "CONDA_PREFIX")
_FORBIDDEN_PYTHON_NAMES = ("maya", "mayapy", "mayabatch")


@dataclass
class ConnectionStatus:
    state: ConnectionState
    message: str = ""
    base_url: str = ""
    owned_pid: Optional[int] = None
    engine_pid: Optional[int] = None
    health: Optional[HealthInfo] = None
    python_executable: str = ""
    timestamp: float = field(default_factory=time.time)


class EngineNotConfigured(Exception):
    pass


class ConnectionManager:
    def __init__(self, config: ConfigManager, events: EventManager,
                 session_path: Optional[Path] = None, root: Optional[Path] = None) -> None:
        self._config = config
        self._events = events
        self._root = root or toolkit_root()
        self._session_path = session_path or session_file()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mait-conn")
        self._lock = threading.RLock()
        self._client: Optional[EngineClient] = None
        self._session: Optional[EngineSession] = None
        self._process: Optional[subprocess.Popen] = None
        self._console_log: Optional[IO[bytes]] = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._closed = False
        self._status = ConnectionStatus(ConnectionState.OFFLINE)
        python = self.resolve_engine_python()
        if python is None:
            self._status = ConnectionStatus(ConnectionState.NOT_CONFIGURED, self._not_configured_message())
        else:
            self._status = ConnectionStatus(ConnectionState.OFFLINE, "Engine offline", python_executable=str(python))

    # ------------------------------------------------------------------ status
    @property
    def status(self) -> ConnectionStatus:
        with self._lock:
            return self._status

    @property
    def state(self) -> ConnectionState:
        return self.status.state

    @property
    def client(self) -> Optional[EngineClient]:
        """The authenticated client while connected, else ``None``."""
        with self._lock:
            return self._client if self._status.state == ConnectionState.CONNECTED else None

    @property
    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    @property
    def owns_engine(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------ engine python
    def resolve_engine_python(self) -> Optional[Path]:
        """Find the engine interpreter: ``engine.python_executable`` or ``<root>/<venv_dir>``. Never Maya's."""
        configured = (self._config.get("engine.python_executable") or "").strip()
        candidates: List[Path] = []
        if configured:
            path = Path(os.path.expandvars(os.path.expanduser(configured)))
            candidates.append(path if path.is_absolute() else self._root / path)
        else:
            venv = Path(os.path.expandvars(os.path.expanduser(self._config.get("engine.venv_dir", ".venv_engine"))))
            venv = venv if venv.is_absolute() else self._root / venv
            candidates += [venv / "Scripts" / "python.exe", venv / "bin" / "python3", venv / "bin" / "python"]
        for candidate in candidates:
            if candidate.is_file():
                if is_maya_interpreter(candidate):
                    log.error("Refusing to use a Maya interpreter as engine Python", extra={"path": str(candidate)})
                    return None
                return candidate
        return None

    def _not_configured_message(self) -> str:
        return ("Engine not configured: run scripts/setup_engine.ps1 to create the engine virtual environment, "
                "or set an engine Python in Settings.")

    def refresh_configuration(self) -> Future:
        """Re-evaluate whether an engine Python is configured (after settings change)."""
        return self._submit(self._refresh_configuration_sync)

    def _refresh_configuration_sync(self, force: bool = False) -> None:
        busy = (ConnectionState.CONNECTED, ConnectionState.STARTING, ConnectionState.STOPPING)
        if not force and self.state in busy:
            return
        python = self.resolve_engine_python()
        if python is None:
            self._set_state(ConnectionState.NOT_CONFIGURED, self._not_configured_message())
        else:
            self._set_state(ConnectionState.OFFLINE, "Engine offline", python_executable=str(python))

    # ------------------------------------------------------------------ public async API
    def start_engine(self) -> Future:
        return self._submit(self._start_engine_sync)

    def stop_engine(self) -> Future:
        return self._submit(self._stop_engine_sync)

    def restart_engine(self) -> Future:
        return self._submit(self._restart_engine_sync)

    def connect(self) -> Future:
        return self._submit(self._connect_sync)

    def disconnect(self) -> Future:
        return self._submit(self._disconnect_sync)

    def health_check(self) -> Future:
        return self._submit(self._health_check_sync)

    def _submit(self, fn: Any) -> Future:
        if self._closed:
            future: Future = Future()
            future.set_result(False)
            return future

        def guarded() -> Any:
            try:
                return fn()
            except Exception as exc:  # last line of defence: never crash the plugin
                log.exception("Connection operation failed")
                self._message("error", "Unexpected connection error: %s" % exc)
                return False

        return self._executor.submit(guarded)

    # ------------------------------------------------------------------ operations (worker thread)
    def _connect_sync(self) -> bool:
        if self.is_connected:
            return True
        python = self.resolve_engine_python()
        try:
            session = read_session_file(self._session_path)
        except ContractError as exc:
            self._set_state(ConnectionState.OFFLINE, "Engine session file is invalid: %s" % exc, level="error")
            return False
        if session is None or not pid_exists(session.pid):
            if session is not None:
                remove_session_file(self._session_path, expected_pid=session.pid)
            state = ConnectionState.OFFLINE if python else ConnectionState.NOT_CONFIGURED
            self._set_state(state, "Engine is not running" if python else self._not_configured_message())
            return False
        self._set_state(ConnectionState.STARTING, "Connecting to engine on port %d" % session.port)
        return self._attach(session)

    def _attach(self, session: EngineSession) -> bool:
        client = EngineClient(session.host, session.port, session.token,
                              timeout=float(self._config.get("engine.request_timeout_seconds", 5.0)))
        try:
            health = client.health()
        except EngineError as exc:
            self._set_state(ConnectionState.OFFLINE, "Engine did not answer health check: %s" % exc, level="error")
            return False
        with self._lock:
            self._client = client
            self._session = session
        self._set_state(ConnectionState.CONNECTED, "Engine connected (v%s, Python %s)"
                        % (health.engine_version, health.python_version), health=health,
                        base_url=session.base_url, engine_pid=session.pid)
        self._events.publish(Events.ENGINE_HEALTH, health)
        self._start_monitor()
        return True

    def _start_engine_sync(self) -> bool:
        if self.is_connected:
            self._message("info", "Engine already connected")
            return True
        # Reuse an engine that is already running (e.g. started via start_engine.bat).
        try:
            existing = read_session_file(self._session_path)
        except ContractError:
            existing = None
        if existing is not None and pid_exists(existing.pid):
            self._message("info", "Found running engine (pid %d); connecting" % existing.pid)
            self._set_state(ConnectionState.STARTING, "Connecting to running engine")
            if self._attach(existing):
                return True
        python = self.resolve_engine_python()
        if python is None:
            self._set_state(ConnectionState.NOT_CONFIGURED, self._not_configured_message())
            return False

        self._set_state(ConnectionState.STARTING, "Starting engine", python_executable=str(python))
        if existing is not None:
            remove_session_file(self._session_path, expected_pid=existing.pid)
        token = secrets.token_urlsafe(32)
        try:
            process = self._spawn(python, token)
        except OSError as exc:
            self._set_state(ConnectionState.OFFLINE, "Could not launch engine: %s" % exc, level="error")
            return False
        with self._lock:
            self._process = process
        self._message("info", "Engine process started (pid %d) with %s" % (process.pid, python))

        deadline = time.monotonic() + float(self._config.get("engine.startup_timeout_seconds", 30))
        while time.monotonic() < deadline and not self._closed:
            code = process.poll()
            if code is not None:
                self._close_console_log()
                self._set_state(ConnectionState.OFFLINE, "Engine exited during startup (code %s). %s"
                                % (code, self._console_tail()), level="error")
                with self._lock:
                    self._process = None
                return False
            try:
                session = read_session_file(self._session_path)
            except ContractError:
                session = None
            if session is not None and session.pid == process.pid and session.token == token:
                if self._attach(session):
                    return True
            time.sleep(0.2)
        self._message("error", "Engine did not become ready in time; terminating it")
        self._terminate_process(process)
        self._set_state(ConnectionState.OFFLINE, "Engine startup timed out. %s" % self._console_tail(), level="error")
        return False

    def _spawn(self, python: Path, token: str) -> subprocess.Popen:
        env = build_engine_env(os.environ, self._root, token)
        config_file = Path(self._config.get("engine.config_file", "config/engine_settings.json"))
        if not config_file.is_absolute():
            config_file = self._root / config_file
        cmd = [str(python), "-m", "ai_engine.main", "--session-file", str(self._session_path),
               "--port", str(int(self._config.get("engine.port", 0)))]
        if config_file.is_file():
            cmd += ["--config", str(config_file)]
        if self._config.get("engine.stop_owned_engine_on_exit", True):
            cmd += ["--parent-pid", str(os.getpid())]
        log_dir = logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        self._close_console_log()
        self._console_log = open(str(log_dir / "engine_console.log"), "wb")
        kwargs: Dict[str, Any] = {"cwd": str(self._root), "env": env, "stdin": subprocess.DEVNULL,
                                  "stdout": self._console_log, "stderr": subprocess.STDOUT, "close_fds": True}
        if sys.platform.startswith("win"):
            # Own process group, no console window popping up over Maya.
            kwargs["creationflags"] = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        log.info("Launching engine", extra={"cmd": cmd})
        return subprocess.Popen(cmd, **kwargs)

    def _stop_engine_sync(self) -> bool:
        with self._lock:
            client, process, session = self._client, self._process, self._session
        if client is None and process is None:
            try:
                session = read_session_file(self._session_path)
            except ContractError:
                session = None
            if session is not None and pid_exists(session.pid):
                client = EngineClient(session.host, session.port, session.token, timeout=3.0)
        if client is None and process is None:
            self._refresh_configuration_sync(force=True)
            self._message("info", "No engine is running")
            return True
        self._set_state(ConnectionState.STOPPING, "Stopping engine")
        self._stop_monitor()
        if client is not None:
            try:
                client.shutdown()
            except EngineError as exc:
                log.warning("Shutdown request failed", extra={"error": str(exc)})
        timeout = float(self._config.get("engine.shutdown_timeout_seconds", 10))
        stopped = True
        if process is not None:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._message("warning", "Engine did not exit gracefully; terminating")
                self._terminate_process(process)
        elif session is not None:
            # Not our child: the engine removes its session file as the last step of a clean shutdown.
            deadline = time.monotonic() + timeout
            while self._engine_alive(session.pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            stopped = not self._engine_alive(session.pid)
        with self._lock:
            self._client = None
            self._session = None
            self._process = None
        self._close_console_log()
        self._refresh_configuration_sync(force=True)
        self._message("info" if stopped else "warning", "Engine stopped" if stopped else "Engine may still be running")
        return stopped

    def _restart_engine_sync(self) -> bool:
        self._stop_engine_sync()
        return self._start_engine_sync()

    def _disconnect_sync(self) -> bool:
        self._stop_monitor()
        with self._lock:
            self._client = None
            self._session = None
        self._refresh_configuration_sync(force=True)
        self._message("info", "Disconnected from engine (engine keeps running)")
        return True

    def _health_check_sync(self) -> Optional[HealthInfo]:
        with self._lock:
            client = self._client
        if client is None:
            self._message("warning", "Health check skipped: engine not connected")
            return None
        try:
            health = client.health()
        except EngineError as exc:
            self._message("error", "Health check failed: %s" % exc)
            return None
        with self._lock:
            self._status.health = health
        self._events.publish(Events.ENGINE_HEALTH, health)
        self._message("info", "Health OK: %d active / %d queued jobs, GPU %s"
                      % (health.active_jobs, health.queued_jobs, "yes" if health.gpu_available else "no"))
        return health

    # ------------------------------------------------------------------ health monitor
    def _start_monitor(self) -> None:
        self._stop_monitor()
        self._monitor_stop = threading.Event()
        thread = threading.Thread(target=self._monitor_loop, args=(self._monitor_stop,),
                                  name="mait-health-monitor", daemon=True)
        self._monitor_thread = thread
        thread.start()

    def _stop_monitor(self) -> None:
        self._monitor_stop.set()
        thread = self._monitor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._monitor_thread = None

    def _monitor_loop(self, stop: threading.Event) -> None:
        interval = float(self._config.get("engine.health_check_interval_seconds", 3.0))
        max_failures = int(self._config.get("engine.health_failures_before_offline", 2))
        failures = 0
        while not stop.wait(interval):
            with self._lock:
                client = self._client
            if client is None:
                return
            try:
                health = client.health(timeout=min(interval, 3.0))
            except EngineError as exc:
                failures += 1
                log.warning("Health check failed", extra={"failures": failures, "error": str(exc)})
                if failures >= max_failures and not stop.is_set():
                    with self._lock:
                        self._client = None
                        self._session = None
                    self._set_state(ConnectionState.OFFLINE, "Lost connection to engine: %s" % exc, level="error")
                    return
                continue
            failures = 0
            with self._lock:
                self._status.health = health
            self._events.publish(Events.ENGINE_HEALTH, health)

    # ------------------------------------------------------------------ shutdown (Maya exit)
    def shutdown(self, stop_owned_engine: Optional[bool] = None) -> None:
        """Release resources. Stops the engine only if this Maya session started it (configurable)."""
        if self._closed:
            return
        if stop_owned_engine is None:
            stop_owned_engine = bool(self._config.get("engine.stop_owned_engine_on_exit", True))
        self._stop_monitor()
        if stop_owned_engine and self.owns_engine:
            try:
                self._executor.submit(self._stop_engine_sync).result(
                    timeout=float(self._config.get("engine.shutdown_timeout_seconds", 10)) + 5)
            except Exception:
                with self._lock:
                    process = self._process
                if process is not None:
                    self._terminate_process(process)
        self._closed = True
        self._executor.shutdown(wait=False)
        self._close_console_log()

    # ------------------------------------------------------------------ helpers
    def _set_state(self, state: ConnectionState, message: str = "", level: str = "info", **fields: Any) -> None:
        with self._lock:
            previous = self._status
            status = ConnectionStatus(
                state=state, message=message,
                base_url=fields.get("base_url", previous.base_url if state == ConnectionState.CONNECTED else ""),
                owned_pid=self._process.pid if self._process is not None and self._process.poll() is None else None,
                engine_pid=fields.get("engine_pid", previous.engine_pid if state == ConnectionState.CONNECTED else None),
                health=fields.get("health", previous.health if state == ConnectionState.CONNECTED else None),
                python_executable=fields.get("python_executable", previous.python_executable))
            self._status = status
        log.info("Engine state: %s" % state.value, extra={"state": state.value, "detail": message})
        self._events.publish(Events.CONNECTION_STATE_CHANGED, status)
        if message:
            self._events.publish(Events.ENGINE_MESSAGE, {"level": level, "message": message})

    def _message(self, level: str, message: str) -> None:
        getattr(log, "warning" if level == "warning" else ("error" if level == "error" else "info"))(message)
        self._events.publish(Events.ENGINE_MESSAGE, {"level": level, "message": message})

    def _engine_alive(self, pid: int) -> bool:
        if not pid_exists(pid):
            return False
        try:
            current = read_session_file(self._session_path)
        except ContractError:
            return True
        return current is not None and current.pid == pid

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass

    def _close_console_log(self) -> None:
        handle, self._console_log = self._console_log, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _console_tail(self, max_chars: int = 600) -> str:
        try:
            data = (logs_dir() / "engine_console.log").read_bytes()[-max_chars:]
            text = data.decode("utf-8", errors="replace").strip()
            return ("Engine output: " + text) if text else ""
        except OSError:
            return ""


def is_maya_interpreter(path: Path) -> bool:
    """True for maya.exe / mayapy.exe / mayabatch and friends."""
    stem = path.name.lower()
    for suffix in (".exe", ".bin"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem in _FORBIDDEN_PYTHON_NAMES


def build_engine_env(base_env: Any, root: Path, token: str) -> Dict[str, str]:
    """Environment for the engine process: Maya's Python variables removed, token injected."""
    env = {k: v for k, v in dict(base_env).items() if k.upper() not in _SCRUBBED_ENV}
    env["PYTHONPATH"] = str(root)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env[ENV_ENGINE_TOKEN] = token
    return env
