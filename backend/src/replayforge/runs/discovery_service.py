"""Application orchestration for model-driven capability discovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from replayforge.capabilities.registry import CapabilityRegistry
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.shared.ids import EntityKind, new_id


class DiscoveryExecutor(Protocol):
    def execute(self, request: DiscoveryRequest) -> DiscoveryResult: ...


DiscoveryExecutorFactory = Callable[[str], DiscoveryExecutor]


@dataclass(frozen=True, slots=True)
class DiscoveryApplicationService:
    registry: CapabilityRegistry
    executor_factory: DiscoveryExecutorFactory
    provider_ready: Callable[[], bool]

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
                max_steps=max_steps,
                timeout=timedelta(seconds=timeout_seconds),
            )
        )
        if isinstance(result, DiscoverySuccess):
            self.registry.publish(result.artifact)
        return result
