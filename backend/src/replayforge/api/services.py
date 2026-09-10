"""Application-service ports consumed by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from replayforge.discovery.models import DiscoveryResult
from replayforge.interventions.models import InterventionFrame
from replayforge.interventions.service import InterventionTransition
from replayforge.runs.results import RunResult


class ReplayInvoker(Protocol):
    def ready(self) -> bool: ...

    def invoke(
        self,
        capability_id: str,
        version: str | None,
        tenant: str,
        inputs: dict[str, Any],
    ) -> RunResult: ...


class DiscoveryInvoker(Protocol):
    def ready(self) -> bool: ...

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
    ) -> DiscoveryResult: ...


class InterventionInvoker(Protocol):
    def get(self, intervention_id: str) -> InterventionTransition: ...

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def viewport(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionFrame: ...

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    replay_invoker: ReplayInvoker
    discovery_invoker: DiscoveryInvoker | None = None
    intervention_invoker: InterventionInvoker | None = None
