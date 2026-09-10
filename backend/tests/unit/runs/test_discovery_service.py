from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import InMemoryCapabilityRegistry
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.runs.discovery_service import DiscoveryApplicationService
from replayforge.runs.results import FailureResult
from replayforge.shared.clock import FrozenClock


@dataclass
class Executor:
    artifact: CapabilityArtifact
    succeed: bool
    request: DiscoveryRequest | None = None

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        self.request = request
        if self.succeed:
            return DiscoverySuccess("success", request.run_id, self.artifact, "evidence://manifest")
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code="provider_unavailable",
            message="Provider unavailable.",
            recoverable=True,
            evidence_manifest="evidence://manifest",
        )


def test_successful_discovery_publishes_artifact(valid_artifact_data: dict[str, Any]) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    registry.publish(artifact)
    executor = Executor(artifact, True)
    service = DiscoveryApplicationService(registry, lambda run_id: executor, lambda: True)

    result = service.invoke(
        goal="Look up the current savings balance",
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=120,
    )

    assert isinstance(result, DiscoverySuccess)
    assert executor.request is not None and executor.request.run_id.startswith("run_")
    assert result.artifact.capability.version == "1.0.1"
    assert registry.get(artifact.capability.id, "1.0.0").artifact == artifact
    assert registry.get(artifact.capability.id, "1.0.1").artifact == result.artifact


def test_failed_discovery_is_not_published(valid_artifact_data: dict[str, Any]) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    service = DiscoveryApplicationService(
        registry, lambda run_id: Executor(artifact, False), lambda: False
    )

    result = service.invoke(
        goal="Look up the current savings balance",
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        entry_point="member_search",
        inputs={},
        max_steps=10,
        timeout_seconds=60,
    )

    assert isinstance(result, FailureResult)
    assert not service.ready()
