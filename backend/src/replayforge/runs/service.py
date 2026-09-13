"""Application orchestration for resolving and executing registered capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import CapabilityRegistry, CapabilityVersionRecord
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.replay.engine import ReplayRequest
from replayforge.runs.results import RunResult
from replayforge.shared.ids import EntityKind, new_id


class ReplayExecutor(Protocol):
    def execute(self, request: ReplayRequest) -> RunResult: ...


ReplayExecutorFactory = Callable[[str, CapabilityVersionRecord], ReplayExecutor]
ReadinessProbe = Callable[[], bool]
ReplayResultFinalizer = Callable[[RunResult], RunResult]


@dataclass(frozen=True, slots=True)
class ReplayApplicationService:
    """Resolves immutable artifacts and creates one isolated execution scope per run."""

    registry: CapabilityRegistry
    executor_factory: ReplayExecutorFactory
    readiness_probes: tuple[ReadinessProbe, ...] = ()
    result_finalizer: ReplayResultFinalizer | None = None

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
        result = executor.execute(
            ReplayRequest(
                run_id=run_id,
                artifact=record.artifact,
                tenant=tenant,
                inputs=inputs,
            )
        )
        return self.result_finalizer(result) if self.result_finalizer is not None else result

    def validate_artifact(
        self, artifact: CapabilityArtifact, tenant: str, inputs: dict[str, Any]
    ) -> RunResult:
        """Execute an unpublished artifact in a fresh deterministic replay scope."""
        run_id = str(new_id(EntityKind.RUN))
        validation_artifact = artifact
        if tenant not in artifact.compatibility.supported_variants:
            validation_artifact = artifact.model_copy(
                update={
                    "compatibility": artifact.compatibility.model_copy(
                        update={
                            "supported_variants": (
                                *artifact.compatibility.supported_variants,
                                tenant,
                            )
                        }
                    ),
                    "provenance": artifact.provenance.model_copy(
                        update={"artifact_content_hash": None}
                    ),
                }
            )
        record = CapabilityVersionRecord(
            artifact=validation_artifact,
            content_hash=artifact_content_hash(validation_artifact),
            published_at=datetime.now(UTC),
        )
        result = self.executor_factory(run_id, record).execute(
            ReplayRequest(
                run_id=run_id,
                artifact=validation_artifact,
                tenant=tenant,
                inputs=inputs,
            )
        )
        return self.result_finalizer(result) if self.result_finalizer is not None else result
