from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import (
    CapabilityNotFoundError,
    CapabilityPublicationError,
    InMemoryCapabilityRegistry,
)
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.runs.discovery_service import DiscoveryApplicationService
from replayforge.runs.results import FailureResult
from replayforge.shared.clock import FrozenClock
from tests.artifacts import sample_artifact


@dataclass
class Executor:
    artifact: CapabilityArtifact
    succeed: bool
    request: DiscoveryRequest | None = None

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        self.request = request
        if self.succeed:
            return DiscoverySuccess(
                "success",
                request.run_id,
                self.artifact,
                f"evidence://{request.run_id}/manifest.json",
            )
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code="provider_unavailable",
            message="Provider unavailable.",
            recoverable=True,
            evidence_manifest=f"evidence://{request.run_id}/manifest.json",
        )


def test_successful_direct_discovery_returns_unpublished_draft(
    valid_artifact_data: dict[str, Any],
) -> None:
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
    assert result.artifact.capability.version == "1.0.0"
    assert registry.get(artifact.capability.id, "1.0.0").artifact == artifact
    with pytest.raises(CapabilityNotFoundError):
        registry.get(artifact.capability.id, "1.0.1")


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


def test_one_shot_discovery_does_not_publish_sensitive_drafts() -> None:
    artifact = sample_artifact(sensitive=True)
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    service = DiscoveryApplicationService(
        registry, lambda run_id: Executor(artifact, True), lambda: True
    )

    result = service.invoke(
        goal="Discover a sensitive operation safely",
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=120,
    )

    assert isinstance(result, DiscoverySuccess)
    with pytest.raises(CapabilityNotFoundError):
        registry.versions(artifact.capability.id)


def test_success_finalizes_the_original_draft_without_allocating_a_version(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    registry.publish(artifact)
    finalized_versions: list[str] = []

    def finalizer(result: DiscoveryResult) -> DiscoveryResult:
        assert isinstance(result, DiscoverySuccess)
        finalized_versions.append(result.artifact.capability.version)
        return result

    service = DiscoveryApplicationService(
        registry,
        lambda run_id: Executor(artifact, True),
        lambda: True,
        finalizer,
    )

    service.invoke(
        goal="Look up the current savings balance",
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        entry_point="member_search",
        inputs={},
        max_steps=10,
        timeout_seconds=60,
    )

    assert finalized_versions == ["1.0.0"]


def test_direct_discovery_never_calls_the_publication_adapter(
    valid_artifact_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    finalized: list[DiscoveryResult] = []

    def fail(_registry: InMemoryCapabilityRegistry, _artifact: CapabilityArtifact) -> None:
        raise CapabilityPublicationError("/private/path must not be exposed")

    monkeypatch.setattr(InMemoryCapabilityRegistry, "publish_next", fail)

    def finalize(result: DiscoveryResult) -> DiscoveryResult:
        finalized.append(result)
        return result

    service = DiscoveryApplicationService(
        registry,
        lambda run_id: Executor(artifact, True),
        lambda: True,
        finalize,
    )

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
    assert finalized == [result]
