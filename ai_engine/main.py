"""Engine entry point.

Run from the toolkit root with the *engine* virtual environment's Python::

    <venv>/Scripts/python.exe -m ai_engine.main            (Windows)
    <venv>/bin/python -m ai_engine.main                    (Linux/macOS)

The session token is read from the ``MAIT_ENGINE_TOKEN`` environment variable
(never from the command line, where other users could see it). If it is not
set, a random token is generated and published only via the session file.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import List, Optional

if __package__ in (None, ""):  # allow ``python ai_engine/main.py``
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_engine import ENGINE_VERSION  # noqa: E402
from ai_engine.server.authentication import generate_token, validate_token_strength  # noqa: E402
from ai_engine.utils.config import ConfigError, load_engine_settings  # noqa: E402
from ai_engine.utils.logging_utils import setup_engine_logging  # noqa: E402
from shared.constants.engine_constants import ENV_ENGINE_TOKEN  # noqa: E402
from shared.paths import logs_dir, session_file  # noqa: E402
from shared.version import MIN_ENGINE_PYTHON  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_BIND_ERROR = 3
EXIT_ENV_ERROR = 4


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai_engine", description="Maya AI Production Toolkit engine")
    parser.add_argument("--config", type=Path, default=None, help="engine settings JSON")
    parser.add_argument("--host", default=None, help="bind address (only 127.0.0.1 is accepted)")
    parser.add_argument("--port", type=int, default=None, help="port (0 = auto)")
    parser.add_argument("--session-file", type=Path, default=None, help="where to publish port/token")
    parser.add_argument("--no-session-file", action="store_true", help="do not write a session file")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits")
    parser.add_argument("--version", action="version", version=ENGINE_VERSION)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if sys.version_info[:2] < MIN_ENGINE_PYTHON:
        sys.stderr.write("Engine requires Python %d.%d+\n" % MIN_ENGINE_PYTHON)
        return EXIT_ENV_ERROR

    overrides: dict = {"server": {}}
    if args.host is not None:
        overrides["server"]["host"] = args.host
    if args.port is not None:
        overrides["server"]["port"] = args.port
    if args.log_level:
        overrides["logging"] = {"level": args.log_level}
    try:
        settings = load_engine_settings(args.config, overrides)
    except ConfigError as exc:
        sys.stderr.write("Configuration error: %s\n" % exc)
        return EXIT_CONFIG_ERROR

    log_cfg = settings["logging"]
    log = setup_engine_logging(log_cfg["level"], args.log_dir or logs_dir(), log_cfg["json_file"],
                               log_cfg["max_bytes"], log_cfg["backup_count"])

    token = os.environ.pop(ENV_ENGINE_TOKEN, None) or generate_token()
    try:
        validate_token_strength(token)
    except ValueError as exc:
        log.error("Invalid session token: %s", exc)
        return EXIT_CONFIG_ERROR

    from ai_engine.core.engine_manager import EngineManager

    session_path = None if args.no_session_file else (args.session_file or session_file())
    try:
        engine = EngineManager(settings, token, session_file=session_path, parent_pid=args.parent_pid)
    except OSError as exc:
        log.error("Could not bind %s:%s - %s", settings["server"]["host"], settings["server"]["port"], exc)
        return EXIT_BIND_ERROR

    def _on_signal(signum: int, _frame: object) -> None:
        engine.request_shutdown(reason="signal %d" % signum)

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass

    engine.serve_forever()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
