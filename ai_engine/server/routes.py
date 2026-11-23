"""API route table and handlers (framework-free).

Each handler receives a :class:`Request` and returns ``(http_status, data)``.
Raise :class:`ApiError` for structured error responses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from shared.constants.engine_constants import ErrorCode
from shared.contracts.errors import ContractError
from shared.contracts.job_contracts import JobRequest
from shared.version import API_PREFIX


class ApiError(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class Request:
    method: str
    path: str
    params: Dict[str, str] = field(default_factory=dict)
    body: Any = None


Handler = Callable[[Request], Tuple[int, Any]]


@dataclass
class Route:
    method: str
    pattern: Pattern[str]
    handler: Handler
    name: str


class Router:
    def __init__(self) -> None:
        self._routes: List[Route] = []

    def add(self, method: str, template: str, handler: Handler, name: str) -> None:
        regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63})", template) + "$"
        self._routes.append(Route(method.upper(), re.compile(regex), handler, name))

    def resolve(self, method: str, path: str) -> Tuple[Route, Dict[str, str]]:
        path_matched = False
        for route in self._routes:
            match = route.pattern.match(path)
            if not match:
                continue
            path_matched = True
            if route.method == method.upper():
                return route, match.groupdict()
        if path_matched:
            raise ApiError(405, ErrorCode.METHOD_NOT_ALLOWED, "method %s not allowed on %s" % (method, path))
        raise ApiError(404, ErrorCode.NOT_FOUND, "no route for %s" % path)


def build_router(engine: Any) -> Router:
    """Create the v1 route table bound to an :class:`~ai_engine.core.engine_manager.EngineManager`."""
    from ai_engine.core.job_queue import InvalidJobState, JobNotFound, QueueClosed, QueueFull
    from ai_engine.core.task_manager import UnknownJobType

    def job_errors(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except JobNotFound as exc:
            raise ApiError(404, ErrorCode.NOT_FOUND, "job %s not found" % exc.args[0])
        except InvalidJobState as exc:
            raise ApiError(409, ErrorCode.INVALID_STATE, str(exc), {"state": exc.state.value})

    def health(_req: Request) -> Tuple[int, Any]:
        return 200, engine.health().to_dict()

    def list_jobs(_req: Request) -> Tuple[int, Any]:
        return 200, {"jobs": [j.to_dict() for j in engine.jobs.list()]}

    def submit_job(req: Request) -> Tuple[int, Any]:
        if engine.is_shutting_down:
            raise ApiError(503, ErrorCode.SHUTTING_DOWN, "engine is shutting down")
        try:
            job_request = JobRequest.from_dict(req.body)
            status = engine.jobs.submit(job_request)
        except ContractError as exc:
            raise ApiError(422, ErrorCode.VALIDATION_ERROR, str(exc), {"field": exc.field_name})
        except UnknownJobType as exc:
            raise ApiError(422, ErrorCode.UNKNOWN_JOB_TYPE, "unknown job_type '%s'" % exc.args[0],
                           {"available": engine.tasks.job_types()})
        except QueueFull as exc:
            raise ApiError(429, ErrorCode.QUEUE_FULL, str(exc))
        except QueueClosed as exc:
            raise ApiError(503, ErrorCode.SHUTTING_DOWN, str(exc))
        return 202, status.to_dict()

    def get_job(req: Request) -> Tuple[int, Any]:
        return 200, job_errors(lambda: engine.jobs.get(req.params["job_id"])).to_dict()

    def cancel_job(req: Request) -> Tuple[int, Any]:
        return 202, job_errors(lambda: engine.jobs.cancel(req.params["job_id"])).to_dict()

    def pause_job(req: Request) -> Tuple[int, Any]:
        return 202, job_errors(lambda: engine.jobs.pause(req.params["job_id"])).to_dict()

    def resume_job(req: Request) -> Tuple[int, Any]:
        return 202, job_errors(lambda: engine.jobs.resume(req.params["job_id"])).to_dict()

    def job_types(_req: Request) -> Tuple[int, Any]:
        return 200, {"job_types": engine.tasks.describe()}

    # ---------------------------------------------------------------- models
    from ai_engine.models.model_manager import ModelBusy, ModelError, UnknownModel

    def model_errors(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except UnknownModel as exc:
            raise ApiError(404, ErrorCode.NOT_FOUND, "model %s not found" % exc.args[0])
        except ModelBusy as exc:
            raise ApiError(409, ErrorCode.INVALID_STATE, str(exc))
        except ModelError as exc:
            raise ApiError(409, ErrorCode.INVALID_STATE, str(exc))

    def list_models(_req: Request) -> Tuple[int, Any]:
        return 200, {"models": [m.to_dict() for m in engine.models.list()], "store_dir": str(engine.models.store_dir)}

    def get_model(req: Request) -> Tuple[int, Any]:
        return 200, model_errors(lambda: engine.models.get(req.params["model_id"])).to_dict()

    def install_model(req: Request) -> Tuple[int, Any]:
        body = {"job_type": "model_install", "params": {"model_id": req.params["model_id"]},
                "label": "Install model " + req.params["model_id"]}
        model_errors(lambda: engine.models.get(req.params["model_id"]))
        return submit_job(Request("POST", req.path, req.params, body))

    def remove_model(req: Request) -> Tuple[int, Any]:
        model_id = req.params["model_id"]
        engine.backends.evict(model_id)
        return 200, model_errors(lambda: engine.models.remove(model_id)).to_dict()

    def capabilities(_req: Request) -> Tuple[int, Any]:
        return 200, engine.capabilities()

    def shutdown(_req: Request) -> Tuple[int, Any]:
        engine.request_shutdown(reason="api")
        return 202, {"status": "shutting_down"}

    router = Router()
    router.add("GET", API_PREFIX + "/health", health, "health")
    router.add("GET", API_PREFIX + "/job-types", job_types, "job_types")
    router.add("GET", API_PREFIX + "/jobs", list_jobs, "list_jobs")
    router.add("POST", API_PREFIX + "/jobs", submit_job, "submit_job")
    router.add("GET", API_PREFIX + "/jobs/{job_id}", get_job, "get_job")
    router.add("POST", API_PREFIX + "/jobs/{job_id}/cancel", cancel_job, "cancel_job")
    router.add("POST", API_PREFIX + "/jobs/{job_id}/pause", pause_job, "pause_job")
    router.add("POST", API_PREFIX + "/jobs/{job_id}/resume", resume_job, "resume_job")
    router.add("GET", API_PREFIX + "/capabilities", capabilities, "capabilities")
    router.add("GET", API_PREFIX + "/models", list_models, "list_models")
    router.add("GET", API_PREFIX + "/models/{model_id}", get_model, "get_model")
    router.add("DELETE", API_PREFIX + "/models/{model_id}", remove_model, "remove_model")
    router.add("POST", API_PREFIX + "/models/{model_id}/install", install_model, "install_model")
    router.add("POST", API_PREFIX + "/shutdown", shutdown, "shutdown")
    return router
