"""Application orchestration for model-driven capability discovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from replayforge.capabilities.registry import CapabilityRegistry
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.policy.types import Risk
from replayforge.runs.results import FailureResult
from replayforge.shared.ids import EntityKind, new_id


class DiscoveryExecutor(Protocol):
    def execute(self, request: DiscoveryRequest) -> DiscoveryResult: ...


DiscoveryExecutorFactory = Callable[[str], DiscoveryExecutor]
DiscoveryResultFinalizer = Callable[[DiscoveryResult], DiscoveryResult]


@dataclass(frozen=True, slots=True)
class DiscoveryApplicationService:
    registry: CapabilityRegistry
    executor_factory: DiscoveryExecutorFactory
    provider_ready: Callable[[], bool]
    result_finalizer: DiscoveryResultFinalizer | None = None

    def ready(self) -> bool:
        try:
            return self.registry.ready() and self.provider_ready()
        except Exception:
            return False

    def invoke(
        self,
        *,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        inputs: dict[str, Any],
        max_steps: int,
        timeout_seconds: int,
        existing_capability_id: str | None = None,
    ) -> DiscoveryResult:
        result = self._execute(
            goal=goal,
            application_family=application_family,
            tenant=tenant,
            entry_point=entry_point,
            inputs=inputs,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            existing_capability_id=existing_capability_id,
        )
        if isinstance(result, DiscoverySuccess):
            if result.artifact.capability.risk is not Risk.READ_ONLY:
                result = FailureResult(
                    status="failure",
                    run_id=result.run_id,
                    code="discovery_suite_required",
                    message=(
                        "Non-read-only discovery requires the reviewed discovery-suite "
                        "approval workflow."
                    ),
                    recoverable=False,
                    evidence_manifest=result.evidence_manifest,
                )
            else:
                published = self.registry.publish_next(result.artifact)
                result = DiscoverySuccess(
                    status="success",
                    run_id=result.run_id,
                    artifact=published.artifact,
                    evidence_manifest=result.evidence_manifest,
                )
        return self.result_finalizer(result) if self.result_finalizer is not None else result

    def discover(
        self,
        *,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        inputs: dict[str, Any],
        max_steps: int,
        timeout_seconds: int,
        existing_capability_id: str | None = None,
    ) -> DiscoveryResult:
        """Run discovery and finalize evidence without publishing the artifact."""
        result = self._execute(
            goal=goal,
            application_family=application_family,
            tenant=tenant,
            entry_point=entry_point,
            inputs=inputs,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            existing_capability_id=existing_capability_id,
        )
        return self.result_finalizer(result) if self.result_finalizer is not None else result

    def _execute(
        self,
        *,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        inputs: dict[str, Any],
        max_steps: int,
        timeout_seconds: int,
        existing_capability_id: str | None,
    ) -> DiscoveryResult:
        run_id = str(new_id(EntityKind.RUN))
        result = self.executor_factory(run_id).execute(
            DiscoveryRequest(
                run_id=run_id,
                goal=goal,
                application_family=application_family,
                tenant=tenant,
                entry_point=entry_point,
                inputs=inputs,
                existing_capability_id=existing_capability_id,
                max_steps=max_steps,
                timeout=timedelta(seconds=timeout_seconds),
            )
        )
        return result
