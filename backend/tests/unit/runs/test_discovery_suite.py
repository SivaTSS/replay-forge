from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    CapabilityArtifact,
    MatchMode,
    TextCondition,
)
from replayforge.capabilities.registry import InMemoryCapabilityRegistry
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.policy.types import Risk
from replayforge.runs.discovery_service import DiscoveryApplicationService
from replayforge.runs.discovery_suite import (
    DiscoverySuite,
    DiscoverySuiteService,
    DiscoverySuiteStatus,
)
from replayforge.runs.results import FailureResult
from replayforge.shared.clock import FrozenClock


class Executor:
    def __init__(self, artifact: CapabilityArtifact) -> None:
        self.artifact = artifact

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        return DiscoverySuccess(
            status="success",
            run_id=request.run_id,
            artifact=self.artifact,
            evidence_manifest="evidence://suite",
        )


def service_for(
    path: str, scenario_artifacts: tuple[CapabilityArtifact, ...] = ()
) -> DiscoverySuiteService:
    artifact = load_artifact_yaml(Path(path).read_text())
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)))
    artifacts = (artifact, *scenario_artifacts)
    call_index = 0

    def executor_factory(_run: str) -> Executor:
        nonlocal call_index
        selected = artifacts[min(call_index, len(artifacts) - 1)]
        call_index += 1
        return Executor(selected)

    discovery = DiscoveryApplicationService(registry, executor_factory, lambda: True)
    return DiscoverySuiteService(discovery, registry, validator=lambda *_args: True)


def test_read_only_suite_publishes_only_at_finalize() -> None:
    service = service_for("capabilities/member.lookup_savings_balance/1.0.0.yaml")
    suite = service.create(
        goal="Look up a member balance",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )

    assert suite.status == "collecting"
    assert suite.suite_id.startswith("sui_")
    snapshot = suite.snapshot()
    assert "12345" not in str(snapshot)
    artifact_snapshot = snapshot["artifact"]
    assert isinstance(artifact_snapshot, dict)
    assert artifact_snapshot["output_fields"] == [
        "member_id",
        "account_type",
        "currency",
        "available_balance",
        "as_of",
    ]
    assert service.finalize(suite.suite_id).status == "published"


def test_suite_rejects_contradictory_lifecycle_state() -> None:
    service = service_for("capabilities/member.lookup_savings_balance/1.0.0.yaml")
    suite = service.create(
        goal="Look up a member balance",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )

    with pytest.raises(ValueError, match="require an artifact"):
        replace(suite, status=DiscoverySuiteStatus.VALIDATED)
    with pytest.raises(ValueError, match="only a published suite"):
        replace(suite, published_version="1.0.0")


def test_suite_snapshot_omits_failure_values_and_free_text() -> None:
    failure = FailureResult(
        status="failure",
        run_id="run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        code="state_mismatch",
        message="Member 12345 was not found.",
        recoverable=False,
        evidence_manifest=("evidence://run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/manifest.json"),
        expected={"member_id": "12345"},
        observed={"screen": "Customer 12345"},
    )
    suite = DiscoverySuite(
        suite_id="sui_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        goal="Find member 12345",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        primary_inputs={"member_id": "12345"},
        primary=failure,
        status=DiscoverySuiteStatus.FAILED,
    )

    snapshot = suite.snapshot()

    assert "12345" not in str(snapshot)
    assert snapshot["primary"] == {
        "status": "failure",
        "run_id": "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "code": "state_mismatch",
        "recoverable": False,
        "evidence_manifest": ("evidence://run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/manifest.json"),
    }


def test_sensitive_suite_is_blocked_without_publication() -> None:
    service = service_for("capabilities/member.lookup_savings_balance/2.0.0.yaml")
    suite = service.create(
        goal="Reach a sensitive confirmation",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )
    try:
        service.finalize(suite.suite_id)
    except ValueError as error:
        assert "cannot be published" in str(error)
    else:
        raise AssertionError("sensitive discovery must not publish")

    assert service.get(suite.suite_id).status == "failed"


def test_reversible_suite_publishes_after_validation() -> None:
    path = "capabilities/member.lookup_savings_balance/1.0.0.yaml"
    artifact = load_artifact_yaml(Path(path).read_text())
    artifact = artifact.model_copy(
        update={
            "capability": artifact.capability.model_copy(update={"risk": Risk.REVERSIBLE}),
            "policy": artifact.policy.model_copy(update={"maximum_risk": Risk.REVERSIBLE}),
        }
    )
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)))
    discovery = DiscoveryApplicationService(registry, lambda _run: Executor(artifact), lambda: True)
    service = DiscoverySuiteService(discovery, registry, validator=lambda *_args: True)
    suite = service.create(
        goal="Temporarily lock a synthetic card",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )

    published = service.finalize(suite.suite_id)

    assert published.status == "published"
    assert service.published_artifact(suite.suite_id).capability.risk.value == "reversible"


def test_finalize_preserves_validated_tenant_variant() -> None:
    service = service_for("capabilities/member.lookup_savings_balance/1.0.0.yaml")
    suite = service.create(
        goal="Look up a member balance",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )
    validated = service.validate(
        suite.suite_id, tenant="new_variant", inputs={"member_id": "12345"}
    )
    assert validated.artifact is not None

    published = service.finalize(suite.suite_id)

    assert published.artifact is not None
    assert "new_variant" in published.artifact.compatibility.supported_variants


def test_scenario_is_bound_to_a_verified_primary_prefix() -> None:
    path = "capabilities/member.lookup_savings_balance/1.0.0.yaml"
    primary_artifact = load_artifact_yaml(Path(path).read_text())
    branch_condition = TextCondition(kind="text", value="No matching member", match=MatchMode.EXACT)
    branch_step = primary_artifact.steps[-1].model_copy(
        update={
            "postconditions": (
                *primary_artifact.steps[-1].postconditions,
                branch_condition,
            )
        }
    )
    branch_artifact = primary_artifact.model_copy(
        update={
            "steps": (*primary_artifact.steps[:-1], branch_step),
            "checkpoint": primary_artifact.checkpoint.model_copy(
                update={
                    "condition": AllCondition(
                        kind="all",
                        conditions=(branch_condition, primary_artifact.checkpoint.condition),
                    )
                }
            ),
            "provenance": primary_artifact.provenance.model_copy(
                update={"artifact_content_hash": None}
            ),
        }
    )
    service = service_for(path, (branch_artifact,))
    suite = service.create(
        goal="Look up a member balance",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        inputs={"member_id": "12345"},
        max_steps=20,
        timeout_seconds=60,
    )
    with_scenario = service.add_scenario(
        suite.suite_id,
        kind="business_outcome",
        goal="Observe the known no-match result",
        inputs={"member_id": "00000"},
        code="member_not_found_again",
        description="The application reports no matching member.",
        max_steps=20,
        timeout_seconds=60,
    )

    assert len(with_scenario.scenarios) == 1
    finalized = service.finalize(suite.suite_id)
    assert finalized.artifact is not None
    assert finalized.artifact.steps[-1].outcome_refs == ("member_not_found_again",)
