"""Tiny thread-safe publish/subscribe bus.

Callbacks run synchronously on the *publisher's* thread, which is often a
background worker. UI code must never touch widgets from a callback directly:
it subscribes through :class:`maya_plugin.ui.bridge.UiBridge`, which re-emits
each event as a Qt signal delivered on the main thread.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Callable, Dict, List, Tuple

from maya_plugin.core.logger import get_logger

log = get_logger("events")

Callback = Callable[[Any], None]


class Events:
    """Event names published by the core and communication layers."""

    CONNECTION_STATE_CHANGED = "connection.state_changed"   # payload: ConnectionStatus
    ENGINE_MESSAGE = "engine.message"                       # payload: {"level", "message"}
    ENGINE_HEALTH = "engine.health"                         # payload: HealthInfo
    JOB_UPDATED = "job.updated"                             # payload: JobStatus
    JOB_ERROR = "job.error"                                 # payload: {"job_id", "message"}
    LOG_RECORD = "log.record"                               # payload: {"ts", "level", "logger", "message"}
    PROJECT_CHANGED = "project.changed"                     # payload: Project | None
    SETTINGS_CHANGED = "settings.changed"                   # payload: {"key", "value"}


class EventManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Tuple[int, Callback]]] = {}
        self._ids = itertools.count(1)

    def subscribe(self, event: str, callback: Callback) -> Callable[[], None]:
        """Register ``callback``; returns a function that unsubscribes it."""
        sub_id = next(self._ids)
        with self._lock:
            self._subs.setdefault(event, []).append((sub_id, callback))

        def unsubscribe() -> None:
            with self._lock:
                self._subs[event] = [(i, cb) for i, cb in self._subs.get(event, []) if i != sub_id]

        return unsubscribe

    def publish(self, event: str, payload: Any = None) -> None:
        with self._lock:
            subscribers = list(self._subs.get(event, []))
        for _sub_id, callback in subscribers:
            try:
                callback(payload)
            except Exception:
                if event != Events.LOG_RECORD:  # avoid recursion through the log handler
                    log.exception("Event subscriber failed", extra={"event": event})

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()
