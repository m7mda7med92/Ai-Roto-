"""Processor base class and the job execution context."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from shared.contracts.errors import ContractError


class JobCancelled(Exception):
    """Raised inside a processor when cancellation was requested."""


class JobContext:
    """Handed to :meth:`BaseProcessor.run`. The *only* way a processor talks to the queue.

    * :meth:`report` - publish progress (0..1) and a status message.
    * :meth:`checkpoint` - honour pause and raise :class:`JobCancelled` if cancelled.
    * :meth:`sleep` - interruptible sleep (wakes immediately on cancel).
    """

    def __init__(self, job_id: str, params: Dict[str, Any], report_cb: Callable[[float, str], None],
                 cancel_event: threading.Event, resume_event: threading.Event,
                 on_pause_state: Optional[Callable[[bool], None]] = None) -> None:
        self.job_id = job_id
        self.params = params
        self._report_cb = report_cb
        self._cancel = cancel_event
        self._resume = resume_event
        self._on_pause_state = on_pause_state or (lambda paused: None)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def report(self, progress: float, message: str = "") -> None:
        self._report_cb(max(0.0, min(1.0, float(progress))), message)

    def checkpoint(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()
        if not self._resume.is_set():
            self._on_pause_state(True)
            while not self._resume.wait(0.1):
                if self._cancel.is_set():
                    raise JobCancelled()
            self._on_pause_state(False)
        if self._cancel.is_set():
            raise JobCancelled()

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._cancel.wait(min(remaining, 0.05)):
                raise JobCancelled()


class BaseProcessor:
    """Base class for all job processors."""

    #: Unique job type handled by this processor, e.g. ``"simulated"``.
    job_type: str = ""
    #: Human readable description shown in clients.
    description: str = ""

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        self.settings = settings or {}

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate/normalise params before the job is queued. Raise ``ContractError`` on bad input."""
        if not isinstance(params, dict):
            raise ContractError("params must be an object", "params")
        return params

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        """Execute the job. Return a JSON-serialisable result dict."""
        raise NotImplementedError
