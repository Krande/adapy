"""A provider that simulates nothing, for tests of the seam itself.

Never registered by core: a test registers an instance, runs it and resets the registry. It
takes a duration and a number of steps, reports progress, rewrites a partial artefact as it goes,
stores final artefacts at the end and stops between steps when cancelled.

It can stand in for any domain: it stores one JSON artefact per required role of its domain,
named ``<role>.json`` (``model.json`` and ``timeline.json`` for robotics), and the last of them is
the one it rewrites while it runs.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from .interfaces import (
    SIMULATION_DOMAINS,
    ProviderDescription,
    RunKind,
    SimulationCase,
    SimulationContext,
    SimulationResult,
)

__all__ = ["FakeSimulationProvider"]


class FakeSimulationProvider:
    """``on_step(index)`` is called after each step's artefact and progress are out, on the run's
    thread: a test blocks in it to look at a run while it is going."""

    def __init__(
        self,
        provider_id: str = "fake",
        *,
        domain: str = "robotics",
        engine: str = "fake",
        on_step: Callable[[int], None] | None = None,
    ) -> None:
        self.id = provider_id
        self.domain = domain
        self.engine = engine
        self.on_step = on_step

    def describe(self) -> ProviderDescription:
        return ProviderDescription(
            title="Fake simulation",
            version="1.0.0",
            description="Counts steps. For tests.",
            kinds=(RunKind("count", "Count steps"),),
            parameters_schema={
                "type": "object",
                "properties": {
                    "duration": {"type": "number", "minimum": 0, "default": 0.0, "title": "Duration (s)"},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 1000, "title": "Steps"},
                    "fail_at": {"type": "integer", "minimum": 1, "title": "Fail at step"},
                    "label": {"type": "string", "enum": ["plain", "verbose"], "default": "plain"},
                    "skip_role": {"type": "string", "title": "Leave out the final artefact of this role"},
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        )

    def run(self, case: SimulationCase, ctx: SimulationContext) -> SimulationResult:
        steps = int(case.parameters["steps"])
        pause = float(case.parameters.get("duration") or 0.0) / steps
        fail_at = case.parameters.get("fail_at")
        input_sizes = {key: len(ctx.read_input(key)) for key in case.inputs}

        # Not validated here: the registry refuses an unknown domain, which a test relies on.
        domain = SIMULATION_DOMAINS.get(self.domain)
        roles = [r.id for r in domain.artefact_roles if r.required] if domain else ["data"]
        live_role = roles[-1]

        done: list[dict] = []
        for index in range(1, steps + 1):
            ctx.raise_if_cancelled()
            if fail_at == index:
                raise RuntimeError(f"step {index} failed on request")
            if pause:
                time.sleep(pause)
            done.append({"step": index})
            ctx.log(f"step {index} of {steps}")
            ctx.store_artefact(
                f"{live_role}.json", json.dumps({"steps": done}).encode("utf-8"), partial=True, role=live_role
            )
            ctx.progress(index / steps, f"step {index}/{steps}")
            if self.on_step is not None:
                self.on_step(index)

        finals = [
            ctx.store_artefact(f"{role}.json", json.dumps({"steps": done}).encode("utf-8"), role=role)
            for role in roles
            if not case.parameters.get("skip_role") == role
        ]
        return SimulationResult(artefacts=tuple(finals), summary={"steps": steps, "inputs": input_sizes})
