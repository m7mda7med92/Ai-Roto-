"""Thread-safe bridge from the event bus to Qt signals.

Core/communication events are published on worker threads. ``UiBridge`` is a
QObject living on the main (GUI) thread; emitting its signals from a worker
thread results in a *queued* delivery, so connected slots always run on
Maya's main thread. This is the only path by which background results reach widgets.
"""
from __future__ import annotations

from typing import Callable, List

from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.ui.qt import QtCore, Signal, is_valid


class UiBridge(QtCore.QObject):
    connection_changed = Signal(object)   # ConnectionStatus
    engine_message = Signal(object)       # {"level", "message"}
    engine_health = Signal(object)        # HealthInfo
    job_updated = Signal(object)          # JobStatus
    job_error = Signal(object)            # {"job_id", "message"}
    log_record = Signal(object)           # {"ts", "level", "logger", "message"}
    project_changed = Signal(object)      # Project | None
    settings_changed = Signal(object)

    def __init__(self, events: EventManager, parent: QtCore.QObject = None) -> None:
        super().__init__(parent)
        self._unsubscribers: List[Callable[[], None]] = []
        mapping = {
            Events.CONNECTION_STATE_CHANGED: self.connection_changed,
            Events.ENGINE_MESSAGE: self.engine_message,
            Events.ENGINE_HEALTH: self.engine_health,
            Events.JOB_UPDATED: self.job_updated,
            Events.JOB_ERROR: self.job_error,
            Events.LOG_RECORD: self.log_record,
            Events.PROJECT_CHANGED: self.project_changed,
            Events.SETTINGS_CHANGED: self.settings_changed,
        }
        for event, signal in mapping.items():
            self._unsubscribers.append(events.subscribe(event, self._forwarder(signal)))
        self.destroyed.connect(self.disconnect_events)

    def _forwarder(self, signal: object) -> Callable[[object], None]:
        def forward(payload: object) -> None:
            if is_valid(self):
                signal.emit(payload)  # type: ignore[attr-defined]
        return forward

    def disconnect_events(self, *_args: object) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
