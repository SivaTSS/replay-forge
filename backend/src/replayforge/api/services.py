"""Application-service ports consumed by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from replayforge.api.contracts import LaunchRequest
from replayforge.capabilities.models import CapabilityArtifact
from replayforge.discovery.models import DiscoveryResult
from replayforge.interventions.models import (
    HumanInputCommand,
    HumanInputReceipt,
    InterventionFrame,
    InterventionRunMode,
)
from replayforge.interventions.service import InterventionResume, InterventionTransition
from replayforge.runs.discovery_suite import DiscoverySuite, ScenarioKind
from replayforge.runs.results import RunResult
from replayforge.runs.viewing import ExecutionViewer


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
        existing_capability_id: str | None = None,
    ) -> DiscoveryResult: ...


class DiscoverySuiteInvoker(Protocol):
    def ready(self) -> bool: ...

    def create(
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
    ) -> DiscoverySuite: ...

    def get(self, suite_id: str) -> DiscoverySuite: ...

    def add_scenario(
        self,
        suite_id: str,
        *,
        kind: ScenarioKind,
        goal: str,
        inputs: dict[str, Any],
        code: str | None,
        description: str | None,
        max_steps: int,
        timeout_seconds: int,
    ) -> DiscoverySuite: ...

    def validate(self, suite_id: str, *, tenant: str, inputs: dict[str, Any]) -> DiscoverySuite: ...

    def finalize(self, suite_id: str) -> DiscoverySuite: ...

    def published_artifact(self, suite_id: str) -> CapabilityArtifact: ...


class InterventionInvoker(Protocol):
    def get(self, intervention_id: str) -> InterventionTransition: ...

    def list_active(
        self, run_mode: InterventionRunMode | None = None
    ) -> tuple[InterventionTransition, ...]: ...

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionResume: ...

    def viewport(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionFrame: ...

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition: ...

    def send_input(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str,
        command: HumanInputCommand,
    ) -> HumanInputReceipt: ...

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition: ...


class ExecutionController(Protocol):
    @property
    def viewer(self) -> ExecutionViewer: ...

    def catalog(self) -> dict[str, Any]: ...

    def start(self, request: LaunchRequest) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    replay_invoker: ReplayInvoker
    discovery_invoker: DiscoveryInvoker | None = None
    intervention_invoker: InterventionInvoker | None = None
    discovery_suite_invoker: DiscoverySuiteInvoker | None = None
    execution_controller: ExecutionController | None = None
