from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import (
    CapabilityNotFoundError,
    CapabilityVersionRecord,
    InMemoryCapabilityRegistry,
)
from replayforge.replay.engine import ReplayRequest
from replayforge.runs.results import FailureResult, RunResult
from replayforge.runs.service import (
    ReadinessProbe,
    ReplayApplicationService,
    ReplayResultFinalizer,
)
from replayforge.shared.clock import FrozenClock


class RecordingExecutor:
    def __init__(self) -> None:
        self.request: ReplayRequest | None = None

    def execute(self, request: ReplayRequest) -> RunResult:
        self.request = request
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code="test_terminal",
            message="Synthetic terminal result.",
            recoverable=False,
            evidence_manifest=f"evidence://{request.run_id}/manifest.json",
        )


def _service(
    artifact_data: dict[str, Any],
    *,
    probes: tuple[ReadinessProbe, ...] = (),
    finalizer: ReplayResultFinalizer | None = None,
) -> tuple[ReplayApplicationService, list[RecordingExecutor]]:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)))
    registry.publish(CapabilityArtifact.model_validate(artifact_data))
    executors: list[RecordingExecutor] = []

    def factory(run_id: str, record: CapabilityVersionRecord) -> RecordingExecutor:
        assert run_id.startswith("run_")
        assert record.artifact.capability.id == "member.lookup_savings_balance"
        executor = RecordingExecutor()
        executors.append(executor)
        return executor

    return ReplayApplicationService(registry, factory, probes, finalizer), executors


@pytest.mark.parametrize("version", ["1.0.0", None])
def test_invocation_resolves_artifact_and_allocates_run(
    valid_artifact_data: dict[str, Any], version: str | None
) -> None:
    service, executors = _service(valid_artifact_data)

    result = service.invoke(
        "member.lookup_savings_balance", version, "harbor_credit_union", {"member_id": "12345"}
    )

    assert result.status == "failure"
    assert result.run_id.startswith("run_")
    assert executors[0].request is not None
    assert executors[0].request.inputs == {"member_id": "12345"}
    assert executors[0].request.tenant == "harbor_credit_union"
    assert executors[0].request.allow_intervention is True


def test_compatibility_validation_is_unattended(valid_artifact_data: dict[str, Any]) -> None:
    service, executors = _service(valid_artifact_data)
    service.validate_artifact(
        CapabilityArtifact.model_validate(valid_artifact_data),
        "harbor_credit_union",
        {"member_id": "12345"},
    )
    assert executors[0].request is not None
    assert executors[0].request.allow_intervention is False


def test_each_invocation_gets_a_fresh_execution_scope(valid_artifact_data: dict[str, Any]) -> None:
    service, executors = _service(valid_artifact_data)

    first = service.invoke("member.lookup_savings_balance", "1.0.0", "harbor_credit_union", {})
    second = service.invoke("member.lookup_savings_balance", "1.0.0", "harbor_credit_union", {})

    assert len(executors) == 2
    assert executors[0] is not executors[1]
    assert first.run_id != second.run_id


def test_unknown_version_does_not_create_executor(valid_artifact_data: dict[str, Any]) -> None:
    service, executors = _service(valid_artifact_data)

    with pytest.raises(CapabilityNotFoundError):
        service.invoke("member.lookup_savings_balance", "9.9.9", "harbor_credit_union", {})

    assert executors == []


def test_readiness_requires_registry_and_every_probe(valid_artifact_data: dict[str, Any]) -> None:
    service, _ = _service(valid_artifact_data, probes=(lambda: True, lambda: False))

    assert not service.ready()


def test_readiness_fails_closed_when_probe_raises(valid_artifact_data: dict[str, Any]) -> None:
    def broken_probe() -> bool:
        raise RuntimeError("dependency unavailable")

    service, _ = _service(valid_artifact_data, probes=(broken_probe,))

    assert not service.ready()


def test_terminal_result_is_finalized_after_execution(valid_artifact_data: dict[str, Any]) -> None:
    finalized: list[str] = []

    def finalizer(result: RunResult) -> RunResult:
        finalized.append(result.run_id)
        return result.model_copy(update={"evidence_manifest": "evidence://final/manifest"})

    service, _ = _service(valid_artifact_data, finalizer=finalizer)

    result = service.invoke("member.lookup_savings_balance", "1.0.0", "harbor_credit_union", {})

    assert isinstance(result, FailureResult)
    assert finalized == [result.run_id]
    assert result.evidence_manifest == "evidence://final/manifest"


def test_unpublished_artifact_validation_uses_an_ephemeral_tenant_allowance(
    valid_artifact_data: dict[str, Any],
) -> None:
    service, executors = _service(valid_artifact_data)
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    original_variants = artifact.compatibility.supported_variants

    service.validate_artifact(artifact, "new_tenant", {"member_id": "12345"})

    request = executors[0].request
    assert request is not None
    assert "new_tenant" in request.artifact.compatibility.supported_variants
    assert artifact.compatibility.supported_variants == original_variants
