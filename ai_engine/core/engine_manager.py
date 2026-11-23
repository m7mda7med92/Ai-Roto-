"""Engine lifecycle: wires config, GPU detection, task registry, job queue and HTTP server.

Startup:  bind server -> write session file -> serve.
Shutdown: (API / signal / parent exit) -> stop HTTP -> cancel jobs -> remove session file.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ai_engine import ENGINE_VERSION
from ai_engine.core.gpu_manager import GpuManager
from ai_engine.core.job_queue import JobQueue
from ai_engine.core.task_manager import TaskManager, build_default_task_manager
from ai_engine.models.model_manager import ModelManager
from ai_engine.segmentation.backends import BackendCache
from ai_engine.server.app import EngineHTTPServer, create_server
from ai_engine.server.routes import build_router
from shared.contracts.job_contracts import HealthInfo
from shared.process_utils import pid_exists
from shared.session import EngineSession, new_session, remove_session_file, write_session_file
from shared.version import API_VERSION

log = logging.getLogger("ai_engine.manager")


class EngineManager:
    def __init__(self, settings: Dict[str, Any], token: str, session_file: Optional[Path] = None,
                 parent_pid: Optional[int] = None, task_manager: Optional[TaskManager] = None) -> None:
        self.settings = settings
        self._token = token
        self._session_file = session_file
        self._parent_pid = parent_pid
        self._started_monotonic = time.monotonic()
        self._shutdown_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._shutdown_done = False
        self.session: Optional[EngineSession] = None

        self.gpu = GpuManager(settings.get("gpu"))
        models_cfg = settings.get("models", {})
        self.models = ModelManager(
            store_dir=Path(models_cfg["store_dir"]) if models_cfg.get("store_dir") else None,
            catalog_path=Path(models_cfg["catalog"]) if models_cfg.get("catalog") else None,
            timeout=float(models_cfg.get("download_timeout_seconds", 30)))
        self.backends = BackendCache(self.models, device=str(models_cfg.get("device", "auto")))
        self.tasks = task_manager or build_default_task_manager(settings, self.models, self.backends)
        jobs_cfg = settings["jobs"]
        self.jobs = JobQueue(self.tasks, jobs_cfg["max_concurrent_jobs"], jobs_cfg["max_queued_jobs"],
                             jobs_cfg["history_limit"])
        server_cfg = settings["server"]
        self.server: EngineHTTPServer = create_server(
            server_cfg["host"], server_cfg["port"], build_router(self), token,
            max_request_bytes=server_cfg["max_request_bytes"],
            request_timeout=server_cfg["request_timeout_seconds"])

    # ------------------------------------------------------------------ properties
    @property
    def host(self) -> str:
        return str(self.server.server_address[0])

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    # ------------------------------------------------------------------ health
    def health(self) -> HealthInfo:
        gpu = self.gpu.info
        counts = self.jobs.counts()
        return HealthInfo(
            status="shutting_down" if self.is_shutting_down else "ok",
            engine_version=ENGINE_VERSION, api_version=API_VERSION,
            python_version=platform.python_version(),
            platform="%s %s" % (platform.system(), platform.release()),
            pid=os.getpid(), uptime_seconds=time.monotonic() - self._started_monotonic,
            gpu_available=gpu.gpu_available, cuda_available=gpu.cuda_available,
            gpu_devices=list(gpu.devices), gpu_detection_method=gpu.method,
            active_jobs=counts["active"], queued_jobs=counts["queued"], job_types=self.tasks.job_types())

    def capabilities(self) -> Dict[str, Any]:
        """What this engine environment can run (optional AI packages, loaded model)."""
        return {"modules": self.backends.capabilities(), "loaded_model": self.backends.loaded(),
                "model_store": str(self.models.store_dir), "job_types": self.tasks.job_types()}

    # ------------------------------------------------------------------ lifecycle
    def serve_forever(self) -> None:
        """Block serving requests until shutdown is requested."""
        # Warm the GPU cache off the request path so the first /health is fast.
        threading.Thread(target=lambda: self.gpu.info, name="mait-gpu-detect", daemon=True).start()
        if self._session_file is not None:
            self.session = new_session(os.getpid(), self.host, self.port, self._token, ENGINE_VERSION, API_VERSION)
            write_session_file(self._session_file, self.session)
        if self._parent_pid:
            threading.Thread(target=self._watch_parent, name="mait-parent-watchdog", daemon=True).start()
        log.info("Engine listening", extra={"host": self.host, "port": self.port, "pid": os.getpid(),
                                            "python": sys.executable})
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            self._finalize()

    def request_shutdown(self, reason: str = "") -> None:
        """Thread-safe, idempotent. Returns immediately; the serve loop exits shortly after."""
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        log.info("Shutdown requested", extra={"reason": reason})
        # server.shutdown() blocks until serve_forever exits, so never call it on a handler thread directly.
        threading.Thread(target=self.server.shutdown, name="mait-shutdown", daemon=True).start()

    def _finalize(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
        self._shutdown_event.set()
        clean = self.jobs.shutdown(timeout=10.0)
        try:
            self.server.server_close()
        except OSError:
            pass
        if self._session_file is not None:
            remove_session_file(self._session_file, expected_pid=os.getpid())
        log.info("Engine stopped", extra={"clean": clean})

    def _watch_parent(self) -> None:
        interval = float(self.settings.get("watchdog", {}).get("parent_check_interval_seconds", 2.0))
        while not self._shutdown_event.wait(interval):
            if not pid_exists(int(self._parent_pid or 0)):
                log.warning("Parent process exited; shutting down", extra={"parent_pid": self._parent_pid})
                self.request_shutdown(reason="parent_exited")
                return
