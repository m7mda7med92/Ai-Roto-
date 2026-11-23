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


def build_default_task_manager(settings: Optional[Dict[str, Any]] = None, model_manager: Any = None,
                               backend_cache: Any = None, pose_cache: Any = None) -> TaskManager:
    """Register the processors available in this phase.

    The AI processors need the engine's model store and backend cache; without them
    (e.g. bare unit tests) only the simulated job is registered.
    """
    from ai_engine.processors.simulated_processor import SimulatedProcessor

    settings = settings or {}
    manager = TaskManager()
    manager.register(SimulatedProcessor(settings.get("simulated_job", {})))
    if model_manager is not None and backend_cache is not None:
        from ai_engine.processors.model_processors import ModelInstallProcessor
        from ai_engine.processors.roto_processors import (MediaProxiesProcessor, RotoTrackProcessor,
                                                          SegmentFrameProcessor)
        media = settings.get("media", {})
        manager.register(ModelInstallProcessor(model_manager, backend_cache))
        manager.register(MediaProxiesProcessor(media))
        manager.register(SegmentFrameProcessor(model_manager, backend_cache, media))
        manager.register(RotoTrackProcessor(model_manager, backend_cache, media))
    if model_manager is not None and pose_cache is not None:
        from ai_engine.processors.pose_processors import PoseTrackProcessor, RotoFitProcessor
        manager.register(PoseTrackProcessor(model_manager, pose_cache))
        manager.register(RotoFitProcessor())
    return manager
