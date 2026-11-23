"""Model installation as an engine job (download progress + cancel through the job queue)."""
from __future__ import annotations

from typing import Any, Dict

from ai_engine.processors.base import BaseProcessor, JobContext
from ai_engine.processors.param_utils import req_str
from shared.contracts.errors import ContractError


class ModelInstallProcessor(BaseProcessor):
    job_type = "model_install"
    description = "Download and install model weights into the engine's model store."

    def __init__(self, model_manager: Any, backend_cache: Any = None) -> None:
        super().__init__({})
        self.models = model_manager
        self.backends = backend_cache

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        model_id = req_str(params, "model_id")
        from ai_engine.models.model_manager import UnknownModel
        try:
            info = self.models.get(model_id)
        except UnknownModel:
            raise ContractError("unknown model '%s'" % model_id, "model_id")
        if info.builtin:
            raise ContractError("'%s' is built in and needs no install" % model_id, "model_id")
        return {"model_id": model_id}

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        model_id = ctx.params["model_id"]
        ctx.report(0.0, "Preparing download of %s" % model_id)
        if self.backends is not None:
            self.backends.evict(model_id)  # never keep stale weights loaded across a re-install
        info = self.models.install(model_id, ctx.report, ctx.checkpoint)
        return {"model": info.to_dict()}
