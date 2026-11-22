"""Registry mapping ``job_type`` -> processor."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from ai_engine.processors.base import BaseProcessor


class UnknownJobType(KeyError):
    pass


class TaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processors: Dict[str, BaseProcessor] = {}

    def register(self, processor: BaseProcessor) -> None:
        if not processor.job_type:
            raise ValueError("processor has no job_type")
        with self._lock:
            if processor.job_type in self._processors:
                raise ValueError("job_type '%s' already registered" % processor.job_type)
            self._processors[processor.job_type] = processor

    def get(self, job_type: str) -> BaseProcessor:
        with self._lock:
            try:
                return self._processors[job_type]
            except KeyError:
                raise UnknownJobType(job_type)

    def job_types(self) -> List[str]:
        with self._lock:
            return sorted(self._processors)

    def describe(self) -> List[Dict[str, str]]:
        with self._lock:
            return [{"job_type": k, "description": p.description} for k, p in sorted(self._processors.items())]


def build_default_task_manager(settings: Optional[Dict[str, Any]] = None) -> TaskManager:
    """Register the processors available in this phase."""
    from ai_engine.processors.simulated_processor import SimulatedProcessor

    settings = settings or {}
    manager = TaskManager()
    manager.register(SimulatedProcessor(settings.get("simulated_job", {})))
    return manager
