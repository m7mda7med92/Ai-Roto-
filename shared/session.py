"""Engine session file: how a running engine advertises its port and token.

The engine writes this file atomically on startup and removes it on clean
shutdown. The Maya plugin reads it to connect to an engine it did not start
itself (for example one launched with ``scripts/start_engine.bat``).

The file contains the session token, so it is written with owner-only
permissions where the OS supports it and lives in the per-user data folder.
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.contracts.errors import ContractError
from shared.contracts.serialization import dumps, loads
from shared.constants.engine_constants import ALLOWED_BIND_HOSTS


@dataclass
class EngineSession:
    pid: int
    host: str
    port: int
    token: str
    engine_version: str
    api_version: str
    started_at: float

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: object) -> "EngineSession":
        if not isinstance(data, dict):
            raise ContractError("session file must contain an object")
        try:
            session = cls(
                pid=int(data["pid"]), host=str(data["host"]), port=int(data["port"]),
                token=str(data["token"]), engine_version=str(data.get("engine_version", "")),
                api_version=str(data.get("api_version", "")),
                started_at=float(data.get("started_at", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError("invalid session file: %s" % exc)
        if session.host not in ALLOWED_BIND_HOSTS:
            raise ContractError("session host %r is not a loopback address" % session.host)
        if not 0 < session.port < 65536 or not session.token:
            raise ContractError("session file has invalid port or token")
        return session


def write_session_file(path: Path, session: EngineSession) -> None:
    """Atomically write ``session`` to ``path`` with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-", dir=str(path.parent))
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: the per-user profile folder ACL protects the file.
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps(session.to_dict()))
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_session_file(path: Path) -> Optional[EngineSession]:
    """Return the session in ``path``, or ``None`` if missing. Raises ``ContractError`` if corrupt."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContractError("cannot read session file: %s" % exc)
    try:
        return EngineSession.from_dict(loads(raw))
    except ValueError as exc:
        raise ContractError("session file is not valid JSON: %s" % exc)


def remove_session_file(path: Path, expected_pid: Optional[int] = None) -> bool:
    """Delete the session file, but only if it still belongs to ``expected_pid``."""
    if expected_pid is not None:
        try:
            current = read_session_file(path)
        except ContractError:
            current = None
        if current is not None and current.pid != expected_pid:
            return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def new_session(pid: int, host: str, port: int, token: str, engine_version: str, api_version: str) -> EngineSession:
    return EngineSession(pid=pid, host=host, port=port, token=token, engine_version=engine_version,
                         api_version=api_version, started_at=time.time())
