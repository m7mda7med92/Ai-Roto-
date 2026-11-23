"""A simulated processing task used to exercise the full job pipeline.

It does no real work: it advances through ``steps`` stages over
``duration_seconds`` while reporting progress, honouring pause and
cancellation. ``fail_at_progress`` forces a failure for error-path tests.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from ai_engine.processors.base import BaseProcessor, JobContext
from shared.contracts.errors import ContractError


class SimulatedProcessingError(RuntimeError):
    pass


class SimulatedProcessor(BaseProcessor):
    job_type = "simulated"
    description = "Simulated processing task (no AI). Reports incremental progress; supports pause/cancel."

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = super().validate_params(params)
        max_duration = float(self.settings.get("max_duration_seconds", 3600.0))
        duration = params.get("duration_seconds", self.settings.get("default_duration_seconds", 10.0))
        steps = params.get("steps", self.settings.get("default_steps", 50))
        fail_at = params.get("fail_at_progress")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration <= max_duration:
            raise ContractError("duration_seconds must be a number in [0, %s]" % max_duration, "duration_seconds")
        if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 10000:
            raise ContractError("steps must be an integer in [1, 10000]", "steps")
        if fail_at is not None and (isinstance(fail_at, bool) or not isinstance(fail_at, (int, float))
                                    or not 0 <= fail_at <= 1):
            raise ContractError("fail_at_progress must be a number in [0, 1]", "fail_at_progress")
        return {"duration_seconds": float(duration), "steps": steps, "fail_at_progress": fail_at}

    def run(self, ctx: JobContext) -> Dict[str, Any]:
        duration = ctx.params["duration_seconds"]
        steps = ctx.params["steps"]
        fail_at = ctx.params.get("fail_at_progress")
        step_time = duration / steps
        started = time.monotonic()
        ctx.report(0.0, "Starting simulated processing")
        for step in range(1, steps + 1):
            ctx.sleep(step_time)
            progress = step / steps
            if fail_at is not None and progress >= fail_at:
                raise SimulatedProcessingError("Simulated failure at %.0f%%" % (progress * 100))
            ctx.report(progress, "Simulated step %d/%d" % (step, steps))
        return {
            "simulated": True,
            "steps_completed": steps,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "summary": "Simulated job finished. No data was produced and no scene was modified.",
        }
