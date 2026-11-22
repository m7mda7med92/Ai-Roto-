"""Application composition root: one instance per Maya session.

Owns config, events, logging, project service, connection manager and job
manager. The UI receives this object and never constructs services itself,
which keeps the UI replaceable and the services testable without Qt.
"""
from __future__ import annotations

import atexit
import threading
from typing import Optional

from maya_plugin.communication.connection_manager import ConnectionManager
from maya_plugin.communication.job_manager import JobManager
from maya_plugin.core.config_manager import ConfigManager
from maya_plugin.core.environment import EnvironmentInfo, detect_environment
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.logger import get_logger, setup_logging
from maya_plugin.core.project_service import ProjectService
from shared.paths import logs_dir
from shared.version import TOOLKIT_VERSION

log = get_logger("app")


class AppManager:
    def __init__(self, config: Optional[ConfigManager] = None, events: Optional[EventManager] = None) -> None:
        self.events = events or EventManager()
        self.config = config or ConfigManager()
        setup_logging(logs_dir(), self.config.get("logging.level", "INFO"),
                      ui_callback=lambda record: self.events.publish(Events.LOG_RECORD, record))
        self.environment: EnvironmentInfo = detect_environment(include_qt=True)
        self.projects = ProjectService(self.config, self.events)
        self.connection = ConnectionManager(self.config, self.events)
        self.jobs = JobManager(self.connection, self.config, self.events,
                               client_version=str(self.environment.maya_version or ""))
        self._shut_down = False
        log.info("Toolkit %s initialised" % TOOLKIT_VERSION,
                 extra={"maya": self.environment.maya_version, "python": self.environment.python_version,
                        "qt": self.environment.qt_binding})
        for warning in self.environment.warnings:
            log.warning(warning)

    def shutdown(self) -> None:
        """Called on Maya exit. Stops an engine this session started (per config)."""
        if self._shut_down:
            return
        self._shut_down = True
        log.info("Toolkit shutting down")
        self.jobs.shutdown()
        self.connection.shutdown()


_instance: Optional[AppManager] = None
_instance_lock = threading.Lock()


def get_app_manager() -> AppManager:
    """Return the per-session AppManager, creating it on first use."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AppManager()
            atexit.register(_instance.shutdown)
        return _instance


def reset_app_manager() -> None:
    """Shut down and forget the singleton (used by tests and by developer reloads)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.shutdown()
            try:
                atexit.unregister(_instance.shutdown)
            except Exception:
                pass
        _instance = None
