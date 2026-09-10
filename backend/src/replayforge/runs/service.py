"""Application orchestration for resolving and executing registered capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from replayforge.capabilities.registry import CapabilityRegistry, CapabilityVersionRecord
from replayforge.replay.engine import ReplayRequest
from replayforge.runs.results import RunResult
from replayforge.shared.ids import EntityKind, new_id


class ReplayExecutor(Protocol):
    def execute(self, request: ReplayRequest) -> RunResult: ...


ReplayExecutorFactory = Callable[[str, CapabilityVersionRecord], ReplayExecutor]
ReadinessProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ReplayApplicationService:
    """Resolves immutable artifacts and creates one isolated execution scope per run."""

    registry: CapabilityRegistry
    executor_factory: ReplayExecutorFactory
    readiness_probes: tuple[ReadinessProbe, ...] = ()

    def ready(self) -> bool:
        try:
            return self.registry.ready() and all(probe() for probe in self.readiness_probes)
        except Exception:
            return False

    def invoke(
        self,
        capability_id: str,
        version: str | None,
        tenant: str,
        inputs: dict[str, Any],
    ) -> RunResult:
        record = (
            self.registry.latest(capability_id)
            if version is None
            else self.registry.get(capability_id, version)
        )
        run_id = str(new_id(EntityKind.RUN))
        executor = self.executor_factory(run_id, record)
        return executor.execute(
            ReplayRequest(
                run_id=run_id,
                artifact=record.artifact,
                tenant=tenant,
                inputs=inputs,
            )
        )
