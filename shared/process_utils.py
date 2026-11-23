"""Cross-platform process helpers (stdlib only)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Optional


def pid_exists(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently running."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return _pid_exists_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _pid_exists_windows(pid: int) -> bool:  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_process_tree(pid: int, process: Optional[Any] = None, grace: float = 3.0) -> None:
    """Terminate ``pid`` and every process it started.

    * Windows: ``taskkill /T /F`` (the venv launcher ``Scripts\\python.exe`` runs the real
      interpreter as a child process, which a plain TerminateProcess would orphan).
    * POSIX: the engine is started in its own session/process group, so the whole group gets
      SIGTERM, then SIGKILL after ``grace`` seconds.

    ``process`` is the ``subprocess.Popen`` for ``pid`` when we own it (it is reaped).
    """
    if pid <= 0:
        return
    if sys.platform.startswith("win"):  # pragma: no cover - exercised on Windows only
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        except (OSError, subprocess.SubprocessError):
            pass
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=grace)
            except (OSError, subprocess.SubprocessError):
                pass
        return
    try:
        group = os.getpgid(pid)
    except OSError:
        group = None
    own_group = group is not None and group == os.getpgid(0)
    targets = ("process",) if group is None or own_group else ("group",)

    def send(sig: int) -> None:
        try:
            if targets == ("group",):
                os.killpg(group, sig)  # type: ignore[arg-type]
            else:
                os.kill(pid, sig)
        except OSError:
            pass

    send(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if process is not None:
            if process.poll() is not None and (targets != ("group",) or not _group_alive(group)):
                return
        elif not pid_exists(pid) and (targets != ("group",) or not _group_alive(group)):
            return
        time.sleep(0.05)
    send(signal.SIGKILL)
    if process is not None:
        try:
            process.wait(timeout=grace)
        except subprocess.SubprocessError:
            pass


def _group_alive(group: Optional[int]) -> bool:
    if group is None:
        return False
    try:
        os.killpg(group, 0)
    except OSError:
        return False
    return True
