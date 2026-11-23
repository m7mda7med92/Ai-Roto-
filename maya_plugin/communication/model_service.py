"""Client-side model management: list, install (as an engine job), remove.

Network calls run on a background worker; results are published as
``Events.MODELS_UPDATED`` so widgets receive them on the main thread via the UI bridge.
Installs are ordinary engine jobs, so they show progress and can be cancelled in the job table.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, List, Optional

from maya_plugin.communication.contracts import ModelInfo, make_job_request
from maya_plugin.communication.engine_client import EngineError
from maya_plugin.core.event_manager import EventManager, Events
from maya_plugin.core.logger import get_logger
from shared.constants.engine_constants import ConnectionState

log = get_logger("models")

MODEL_INSTALL_JOB = "model_install"


INSTALL_LABEL_PREFIX = "Install model "


def install_label(model_id: str) -> str:
    """Job label for installs; the model id is recovered from it by :func:`model_id_from_label`."""
    return INSTALL_LABEL_PREFIX + model_id


def model_id_from_label(label: str) -> str:
    return label[len(INSTALL_LABEL_PREFIX):] if label.startswith(INSTALL_LABEL_PREFIX) else ""


class ModelService:
    def __init__(self, connection: Any, jobs: Any, events: EventManager) -> None:
        self._connection = connection
        self._jobs = jobs
        self._events = events
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mait-models")
        self._lock = threading.Lock()
        self._models: List[ModelInfo] = []
        self._closed = False
        self._unsubscribe = [
            events.subscribe(Events.CONNECTION_STATE_CHANGED, self._on_connection),
            events.subscribe(Events.JOB_UPDATED, self._on_job),
        ]

    # ------------------------------------------------------------------ queries
    def models(self) -> List[ModelInfo]:
        with self._lock:
            return list(self._models)

    def installed(self, task: Optional[str] = None) -> List[ModelInfo]:
        return [m for m in self.models() if m.installed and (task is None or m.task == task)]

    # ------------------------------------------------------------------ commands (non-blocking)
    def refresh(self) -> Future:
        return self._run(lambda client: client.list_models())

    def install(self, model_id: str) -> Future:
        """Submit a ``model_install`` job; progress arrives through the job manager."""
        return self._jobs.submit(make_job_request(MODEL_INSTALL_JOB, {"model_id": model_id}, install_label(model_id)))

    def remove(self, model_id: str) -> Future:
        def task(client: Any) -> List[ModelInfo]:
            client.remove_model(model_id)
            log.info("Model removed: %s" % model_id)
            return client.list_models()
        return self._run(task)

    def shutdown(self) -> None:
        self._closed = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------ internals
    def _run(self, fn: Callable[[Any], List[ModelInfo]]) -> Future:
        def task() -> Optional[List[ModelInfo]]:
            client = self._connection.client
            if client is None:
                self._publish("Engine not connected")
                return None
            try:
                models = fn(client)
            except EngineError as exc:
                log.error("Model request failed: %s" % exc)
                self._publish(str(exc))
                return None
            with self._lock:
                self._models = list(models)
            self._publish("")
            return models

        if self._closed:
            future: Future = Future()
            future.set_result(None)
            return future
        return self._executor.submit(task)

    def _publish(self, error: str) -> None:
        self._events.publish(Events.MODELS_UPDATED, {"models": self.models(), "error": error})

    def _on_connection(self, status: object) -> None:
        state = getattr(status, "state", None)
        if state == ConnectionState.CONNECTED:
            self.refresh()
        elif state in (ConnectionState.OFFLINE, ConnectionState.NOT_CONFIGURED):
            with self._lock:
                self._models = []
            self._publish("")

    def _on_job(self, status: object) -> None:
        if getattr(status, "job_type", "") == MODEL_INSTALL_JOB and getattr(status, "is_terminal", False):
            self.refresh()
