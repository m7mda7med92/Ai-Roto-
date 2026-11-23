"""Environment diagnostics for the Maya AI Production Toolkit.

Run with any Python 3.7+ (or inside Maya's Script Editor)::

    python scripts/diagnostics.py               # static checks
    python scripts/diagnostics.py --live        # also start the engine, run + cancel a job, stop it
    python scripts/diagnostics.py --json        # machine-readable output

Nothing here modifies Maya preferences or the Maya scene. ``--live`` starts
and stops a private engine instance (separate session file, random port).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


class Report:
    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.items.append({"status": status, "check": check, "detail": detail})

    @property
    def failed(self) -> bool:
        return any(i["status"] == FAIL for i in self.items)


def engine_python() -> Optional[Path]:
    from maya_plugin.core.config_manager import ConfigManager
    configured = (ConfigManager().get("engine.python_executable") or "").strip()
    if configured:
        path = Path(os.path.expandvars(os.path.expanduser(configured)))
        return path if path.is_file() else None
    venv = ROOT / ".venv_engine"
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python3", venv / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def check_static(report: Report) -> None:
    report.add(INFO, "Operating system", "%s %s (%s)" % (platform.system(), platform.release(), platform.machine()))
    report.add(OK if sys.version_info >= (3, 7) else FAIL, "This Python", "%s at %s" % (
        platform.python_version(), sys.executable))
    report.add(INFO, "Toolkit root", str(ROOT))
    for rel in ("maya_plugin", "ai_engine", "shared", "plug-ins/MayaAIToolkit.py", "config/default_settings.json",
                "config/engine_settings.json", "config/model_catalog.json",
                "maya_plugin/resources/shelves/shelf_MayaAIToolkit.mel"):
        report.add(OK if (ROOT / rel).exists() else FAIL, "File present: %s" % rel)
    for rel in ("config/default_settings.json", "config/engine_settings.json"):
        try:
            json.loads((ROOT / rel).read_text(encoding="utf-8"))
            report.add(OK, "Valid JSON: %s" % rel)
        except (OSError, ValueError) as exc:
            report.add(FAIL, "Valid JSON: %s" % rel, str(exc))
    try:
        from ai_engine.utils.config import load_engine_settings
        settings = load_engine_settings(ROOT / "config" / "engine_settings.json")
        report.add(OK, "Engine settings validate", "bind %s port %s" % (settings["server"]["host"],
                                                                        settings["server"]["port"] or "auto"))
    except Exception as exc:
        report.add(FAIL, "Engine settings validate", str(exc))

    # Maya
    try:
        from maya import cmds  # type: ignore
        report.add(INFO, "Running inside Maya", "Maya %s, API %s" % (cmds.about(version=True), cmds.about(apiVersion=True)))
    except Exception:
        maya_location = os.environ.get("MAYA_LOCATION")
        report.add(INFO, "Running inside Maya", "no (MAYA_LOCATION=%s)" % (maya_location or "unset"))
    try:
        from maya_plugin.ui.qt import QT_BINDING, QT_BINDING_VERSION, QtCore
        report.add(INFO, "Qt binding available to this Python", "%s %s (Qt %s)" % (
            QT_BINDING, QT_BINDING_VERSION, QtCore.qVersion()))
    except Exception as exc:
        report.add(INFO, "Qt binding available to this Python", "none (%s) - expected outside Maya" % exc)

    # Installation
    from shared.paths import session_file, user_dir
    info_path = user_dir() / "install_info.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            mod = Path(info.get("module_file", ""))
            report.add(OK if mod.exists() else WARN, "Maya module installed", "%s (toolkit %s)" % (
                mod, info.get("toolkit_root")))
            if Path(info.get("toolkit_root", "")) != ROOT:
                report.add(WARN, "Install location", "installed copy is %s, diagnostics run from %s" % (
                    info.get("toolkit_root"), ROOT))
        except (OSError, ValueError) as exc:
            report.add(WARN, "Maya module installed", "unreadable install_info.json: %s" % exc)
    else:
        report.add(WARN, "Maya module installed", "not installed (run scripts/install_maya_plugin.py)")

    # Engine environment
    python = engine_python()
    if python is None:
        report.add(WARN, "Engine Python", "not found - run scripts/setup_engine.ps1")
    else:
        from maya_plugin.communication.connection_manager import is_maya_interpreter
        if is_maya_interpreter(python):
            report.add(FAIL, "Engine Python", "%s is a Maya interpreter; the engine needs its own venv" % python)
        else:
            try:
                out = subprocess.run([str(python), "-c", "import sys; print(sys.version.split()[0])"],
                                     capture_output=True, text=True, timeout=20)
                version = out.stdout.strip()
                ok = tuple(int(p) for p in version.split(".")[:2]) >= (3, 10)
                report.add(OK if ok else FAIL, "Engine Python", "%s (Python %s)" % (python, version))
                check_engine_packages(report, python)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                report.add(FAIL, "Engine Python", "%s cannot run: %s" % (python, exc))

    # Models
    try:
        from ai_engine.models.model_manager import ModelManager
        manager = ModelManager()
        installed = [m.name for m in manager.list() if m.installed]
        report.add(OK, "Model catalog", "%d models; installed: %s (store %s)" % (
            len(manager.catalog), ", ".join(installed) or "none", manager.store_dir))
    except Exception as exc:
        report.add(FAIL, "Model catalog", str(exc))

    # GPU
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10)
            report.add(OK if out.returncode == 0 else WARN, "NVIDIA GPU", out.stdout.strip() or out.stderr.strip())
        except (OSError, subprocess.SubprocessError) as exc:
            report.add(WARN, "NVIDIA GPU", "nvidia-smi failed: %s" % exc)
    else:
        report.add(WARN, "NVIDIA GPU", "nvidia-smi not found (no NVIDIA driver?) - engine will run CPU-only")

    # Loopback networking
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        report.add(OK, "Loopback bind", "127.0.0.1:%d available" % port)
    except OSError as exc:
        report.add(FAIL, "Loopback bind", str(exc))
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if proxy:
        report.add(INFO, "HTTP proxy", "%s is set; the toolkit bypasses proxies for 127.0.0.1" % proxy)

    sess = session_file()
    report.add(INFO, "Engine session file", "%s (%s)" % (sess, "present" if sess.exists() else "absent"))


PACKAGE_PROBE = (
    "import json, importlib\n"
    "out = {}\n"
    "for name in ('numpy', 'cv2', 'truststore', 'torch', 'sam2'):\n"
    "    try:\n"
    "        mod = importlib.import_module(name)\n"
    "        out[name] = getattr(mod, '__version__', 'installed')\n"
    "    except Exception as exc:\n"
    "        out[name] = None\n"
    "try:\n"
    "    import torch\n"
    "    out['cuda'] = bool(torch.cuda.is_available())\n"
    "except Exception:\n"
    "    out['cuda'] = None\n"
    "print(json.dumps(out))\n"
)


def check_engine_packages(report: Report, python: Path) -> None:
    """Which AI packages the engine venv has (never imports them into this process)."""
    try:
        out = subprocess.run([str(python), "-I", "-c", PACKAGE_PROBE], capture_output=True, text=True, timeout=120)
        found = json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        report.add(WARN, "Engine packages", "probe failed: %s" % exc)
        return
    core = all(found.get(name) for name in ("numpy", "cv2"))
    report.add(OK if core else FAIL, "Engine packages (core)", "numpy %s, opencv %s%s" % (
        found.get("numpy"), found.get("cv2"), "" if core else " - re-run scripts/setup_engine.ps1"))
    if found.get("truststore"):
        report.add(OK, "Engine HTTPS trust", "truststore %s: model downloads use the Windows certificate store"
                   % found["truststore"])
    else:
        report.add(WARN, "Engine HTTPS trust", "truststore not installed: model downloads may fail on networks "
                   "that inspect SSL - re-run scripts/setup_engine.ps1")
    if found.get("torch") and found.get("sam2"):
        report.add(OK, "Engine packages (SAM 2)", "torch %s, sam2 %s, CUDA available: %s" % (
            found["torch"], found["sam2"], found.get("cuda")))
    else:
        report.add(INFO, "Engine packages (SAM 2)", "not installed (optional: setup_engine.ps1 -WithSam2)")


def check_live(report: Report) -> None:
    """Start a private engine with the engine venv, run and cancel a simulated job, shut it down."""
    python = engine_python() or (Path(sys.executable) if os.environ.get("MAIT_DIAG_USE_CURRENT_PYTHON") else None)
    if python is None:
        report.add(FAIL, "Live engine test", "no engine Python (run scripts/setup_engine.ps1)")
        return
    from maya_plugin.communication.connection_manager import build_engine_env
    from maya_plugin.communication.engine_client import EngineClient, EngineError
    from maya_plugin.communication.contracts import make_test_job_request
    from shared.session import read_session_file

    token = __import__("secrets").token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="mait-diag-") as tmp:
        session_path = Path(tmp) / "session.json"
        proc = subprocess.Popen([str(python), "-m", "ai_engine.main", "--port", "0", "--session-file",
                                 str(session_path), "--log-dir", tmp], cwd=str(ROOT),
                                env=build_engine_env(os.environ, ROOT, token),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            session = None
            deadline = time.time() + 30
            while time.time() < deadline and proc.poll() is None:
                session = read_session_file(session_path)
                if session:
                    break
                time.sleep(0.2)
            if session is None:
                report.add(FAIL, "Live: engine start", "engine did not start (exit code %s)" % proc.poll())
                return
            report.add(OK, "Live: engine start", "pid %d on 127.0.0.1:%d" % (session.pid, session.port))
            client = EngineClient(session.host, session.port, session.token)
            health = client.health()
            report.add(OK, "Live: health", "engine %s, Python %s, GPU %s, CUDA %s" % (
                health.engine_version, health.python_version, health.gpu_available, health.cuda_available))
            bad = EngineClient(session.host, session.port, "x" * 43)
            try:
                bad.health()
                report.add(FAIL, "Live: auth rejects bad token")
            except EngineError as exc:
                report.add(OK if exc.status == 401 else FAIL, "Live: auth rejects bad token", "HTTP %s" % exc.status)
            job = client.submit_job(make_test_job_request(duration_seconds=5, steps=50, label="diagnostics"))
            time.sleep(1.0)
            progress = client.get_job(job.job_id).progress
            client.cancel_job(job.job_id)
            state = None
            for _ in range(50):
                state = client.get_job(job.job_id).state.value
                if state == "cancelled":
                    break
                time.sleep(0.1)
            report.add(OK if progress > 0 and state == "cancelled" else FAIL, "Live: job progress + cancel",
                       "progress %.0f%% then %s" % (progress * 100, state))
            client.shutdown()
            proc.wait(timeout=15)
            report.add(OK if proc.returncode == 0 else FAIL, "Live: clean shutdown", "exit code %s" % proc.returncode)
        except Exception as exc:
            report.add(FAIL, "Live engine test", "%s: %s" % (type(exc).__name__, exc))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Maya AI Toolkit diagnostics")
    parser.add_argument("--live", action="store_true", help="start the engine and run a job round-trip")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = Report()
    check_static(report)
    if args.live:
        check_live(report)
    if args.json:
        print(json.dumps({"ok": not report.failed, "checks": report.items}, indent=2))
    else:
        width = max(len(i["check"]) for i in report.items)
        for item in report.items:
            print("[%-4s] %-*s  %s" % (item["status"], width, item["check"], item["detail"]))
        print("\nResult: %s" % ("PROBLEMS FOUND" if report.failed else "no failures"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
