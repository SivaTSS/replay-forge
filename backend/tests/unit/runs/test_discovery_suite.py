from dataclasses import replace
from datetime import UTC, datetime

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    AssertAction,
    CapabilityArtifact,
    ElementCondition,
    MatchMode,
    TextCondition,
)
from replayforge.capabilities.registry import InMemoryCapabilityRegistry
from replayforge.discovery.engine import DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess, ObservedBranch
from replayforge.policy.types import Risk
from replayforge.runs.discovery_service import DiscoveryApplicationService
from replayforge.runs.discovery_suite import (
    DiscoveryScenario,
    DiscoverySuite,
    DiscoverySuiteService,
    DiscoverySuiteStatus,
    ReplayValidation,
    _merge_scenarios,
    _shared_prefix_length,
)
from replayforge.runs.results import (
    ArtifactPrivacyDiagnostic,
    BusinessOutcomeResult,
    CapabilityReference,
    FailureResult,
    SuccessResult,
    VerifiedCheckpoint,
)
from replayforge.shared.clock import FrozenClock
from tests.artifacts import sample_artifact


def successful_validation(*_args: object) -> ReplayValidation:
    return ReplayValidation(
        SuccessResult(
            status="success",
            run_id="run_" + "a" * 32,
            evidence_manifest="evidence://run_" + "a" * 32 + "/manifest.json",
            capability=CapabilityReference(id="member.test", version="1.0.0"),
            outputs={},
            checkpoint=VerifiedCheckpoint(id="verified", verified=True),
        )
    )


class Executor:
    def __init__(self, artifact: CapabilityArtifact) -> None:
        self.artifact = artifact

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        marker = self.artifact.steps[-1].action
        return DiscoverySuccess(
            status="success",
            run_id=request.run_id,
            artifact=self.artifact,
            evidence_manifest=f"evidence://{request.run_id}/manifest.json",
            branch=(
                ObservedBranch(len(self.artifact.steps) - 1, marker.condition)
                if request.scenario and isinstance(marker, AssertAction)
                else None
            ),
        )


def service_for(
    artifact: CapabilityArtifact, scenario_artifacts: tuple[CapabilityArtifact, ...] = ()
) -> DiscoverySuiteService:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)))
    artifacts = (artifact, *scenario_artifacts)
    call_index = 0

    def executor_factory(_run: str) -> Executor:
        nonlocal call_index
        selected = artifacts[min(call_index, len(artifacts) - 1)]
        call_index += 1
        return Executor(selected)

    discovery = DiscoveryApplicationService(registry, executor_factory, lambda: True)
    return DiscoverySuiteService(discovery, registry, validator=successful_validation)


def test_read_only_suite_publishes_only_at_finalize() -> None:
    service = service_for(sample_artifact())
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


def test_extend_published_preserves_real_provenance_without_new_model_calls() -> None:
    artifact = sample_artifact()
    service = service_for(artifact)
    service.registry.publish(artifact)
    calls: list[str] = []

    def validate(
        artifact: CapabilityArtifact, tenant: str, inputs: dict[str, object]
    ) -> ReplayValidation:
        calls.append(tenant)
        return successful_validation()

    service.validator = validate
    suite = service.from_published(
        capability_id=artifact.capability.id,
        version=artifact.capability.version,
        tenant="harbor",
        inputs={"member_id": "12345"},
    )
    assert calls == ["harbor"]
    assert suite.snapshot()["primary_source"] == "published_capability"
    assert isinstance(suite.primary, DiscoverySuccess)
    assert suite.primary.run_id == artifact.provenance.discovery_run_id
    assert suite.primary.evidence_manifest == artifact.provenance.evidence_manifest_key
    assert suite.primary.artifact == artifact
    assert suite.status == "collecting"


@pytest.mark.parametrize("failure", ["missing", "no_validator", "replay_failed"])
def test_extend_published_requires_available_version_and_fresh_success(failure: str) -> None:
    artifact = sample_artifact()
    service = service_for(artifact)
    if failure != "missing":
        service.registry.publish(artifact)
    if failure == "no_validator":
        service.validator = None
    elif failure == "replay_failed":
        service.validator = lambda *_args: ReplayValidation(
            FailureResult(
                status="failure",
                run_id="run_" + "a" * 32,
                code="target_absent",
                message="Missing",
                recoverable=False,
                evidence_manifest="evidence://run_" + "a" * 32 + "/manifest.json",
            )
        )
    with pytest.raises(ValueError):
        service.from_published(
            capability_id=artifact.capability.id,
            version=artifact.capability.version,
            tenant="harbor",
            inputs={"member_id": "12345"},
        )


@pytest.mark.parametrize("state", ["absent", "hidden"])
def test_missing_control_cannot_prove_negative_business_outcome(state: str) -> None:
    primary = sample_artifact()
    condition = ElementCondition.model_validate(
        {
            "kind": "element",
            "state": state,
            "target": primary.steps[0].target.model_dump() if primary.steps[0].target else {},
        }
    )
    marker = primary.steps[0].model_copy(
        update={
            "id": "negative_marker",
            "action": AssertAction(kind="assert", condition=condition),
            "target": None,
            "postconditions": (condition,),
        }
    )
    traced = primary.model_copy(update={"steps": (*primary.steps, marker)})
    scenario = DiscoveryScenario(
        kind="business_outcome",
        goal="Observe a missing record",
        code="missing_record",
        description="No matching record",
        inputs={"member_id": "00000"},
        result=DiscoverySuccess(
            status="success",
            run_id=primary.provenance.discovery_run_id,
            artifact=traced,
            evidence_manifest=primary.provenance.evidence_manifest_key,
            branch=ObservedBranch(len(primary.steps), condition),
        ),
    )
    with pytest.raises(ValueError, match="positive distinctive"):
        _merge_scenarios(primary, (scenario,))


def test_suite_rejects_contradictory_lifecycle_state() -> None:
    service = service_for(sample_artifact())
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

    diagnostic = ArtifactPrivacyDiagnostic(
        source="captured", location="artifact.steps[2].target.description"
    )
    failed = replace(
        suite,
        primary=failure.model_copy(
            update={"code": "artifact_privacy_rejected", "privacy_rejection": diagnostic}
        ),
    )
    primary_snapshot = failed.snapshot()["primary"]
    assert isinstance(primary_snapshot, dict)
    assert primary_snapshot["privacy_rejection"] == diagnostic.model_dump()
    assert "12345" not in str(failed.snapshot())


def test_sensitive_suite_is_blocked_without_publication() -> None:
    service = service_for(sample_artifact(sensitive=True))
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
    artifact = sample_artifact()
    artifact = artifact.model_copy(
        update={
            "capability": artifact.capability.model_copy(update={"risk": Risk.REVERSIBLE}),
            "policy": artifact.policy.model_copy(update={"maximum_risk": Risk.REVERSIBLE}),
        }
    )
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)))
    discovery = DiscoveryApplicationService(registry, lambda _run: Executor(artifact), lambda: True)
    service = DiscoverySuiteService(discovery, registry, validator=successful_validation)
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
    service = service_for(sample_artifact())
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
    primary_artifact = sample_artifact()
    branch_condition = TextCondition(kind="text", value="No matching member", match=MatchMode.EXACT)
    branch_step = primary_artifact.steps[-1].model_copy(
        update={
            "id": "branch_marker",
            "action": AssertAction(kind="assert", condition=branch_condition),
            "target": None,
            "postconditions": (branch_condition,),
        }
    )
    branch_artifact = primary_artifact.model_copy(
        update={
            "steps": (*primary_artifact.steps, branch_step),
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
    service = service_for(primary_artifact, (branch_artifact,))
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
    assert with_scenario.scenarios[0].inputs == {"member_id": "00000"}
    assert "00000" not in str(with_scenario.snapshot())
    # Happy-path success is not evidence that a negative branch ran.
    with pytest.raises(ValueError, match="scenario replay did not verify"):
        service.finalize(suite.suite_id)
    assert service.get(suite.suite_id).published_version is None

    def validate(
        _artifact: CapabilityArtifact, _tenant: str, inputs: dict[str, object]
    ) -> ReplayValidation:
        if inputs["member_id"] == "00000":
            return ReplayValidation(
                BusinessOutcomeResult(
                    status="business_outcome",
                    code="member_not_found_again",
                    details={},
                    run_id="run_" + "b" * 32,
                    evidence_manifest="evidence://run_" + "b" * 32 + "/manifest.json",
                )
            )
        return successful_validation()

    service.validator = validate
    finalized = service.finalize(suite.suite_id)
    assert finalized.artifact is not None
    assert finalized.artifact.steps[-1].outcome_refs == ("member_not_found_again",)


def test_prefix_identity_includes_target_and_scope_not_just_action() -> None:
    primary = sample_artifact()
    original = primary.steps[0]
    assert original.target is not None
    described = original.model_copy(
        update={
            "target": original.target.model_copy(
                update={"description": "Same target, different descriptive prose"}
            )
        }
    )
    alternate = primary.model_copy(update={"steps": (described, *primary.steps[1:])})
    assert _shared_prefix_length(primary, alternate) == len(primary.steps)
    changed = original.model_copy(
        update={
            "target": original.target.model_copy(
                update={"state": original.target.state.model_copy(update={"enabled": True})}
            )
        }
    )
    alternate = primary.model_copy(update={"steps": (changed, *primary.steps[1:])})
    assert _shared_prefix_length(primary, alternate) == 0


def test_recovery_validation_requires_completed_named_recovery() -> None:
    result = Executor(sample_artifact()).execute(
        DiscoveryRequest(
            run_id="run_" + "c" * 32,
            goal="Verify a generic recovery",
            application_family="example_app",
            tenant="tenant_a",
            entry_point="start",
            inputs={},
        )
    )
    scenario = DiscoveryScenario(
        "recovery",
        "Recover a transient notice",
        "dismiss_notice",
        "Restore the original workflow",
        result,
        {},
    )
    happy = successful_validation()
    assert not happy.verifies(scenario)
    assert not ReplayValidation(happy.result, ("different_recovery",)).verifies(scenario)
    assert ReplayValidation(happy.result, ("dismiss_notice",)).verifies(scenario)
    assert not ReplayValidation(happy.result, ("dismiss_notice", "different_recovery")).verifies(
        scenario
    )
    assert not ReplayValidation(happy.result, ("dismiss_notice", "dismiss_notice")).verifies(
        scenario
    )
    assert happy.verifies()
    assert not ReplayValidation(happy.result, ("dismiss_notice",)).verifies()


def test_recovery_cannot_use_checkpoint_boilerplate_as_restoration_evidence() -> None:
    primary = sample_artifact()
    condition = TextCondition(kind="text", value="A blocking notice")
    marker = primary.steps[0].model_copy(
        update={
            "id": "marker",
            "action": AssertAction(kind="assert", condition=condition),
            "target": None,
            "postconditions": (condition,),
        }
    )
    correction = marker.model_copy(update={"id": "still_the_same_notice"})
    trace = primary.model_copy(update={"steps": (primary.steps[0], marker, correction)})
    scenario = DiscoveryScenario(
        "recovery",
        "Correct a notice",
        "dismiss_notice",
        "Restore",
        DiscoverySuccess(
            status="success",
            run_id=primary.provenance.discovery_run_id,
            artifact=trace,
            evidence_manifest=primary.provenance.evidence_manifest_key,
            branch=ObservedBranch(1, condition),
        ),
        {"member_id": "12345"},
    )
    with pytest.raises(ValueError, match="distinct verified restored state"):
        _merge_scenarios(primary, (scenario,))


def test_negative_validation_requires_exact_disposition_and_code() -> None:
    artifact = sample_artifact()
    discovered = DiscoverySuccess(
        status="success",
        run_id="run_" + "d" * 32,
        artifact=artifact,
        evidence_manifest="evidence://run_" + "d" * 32 + "/manifest.json",
    )
    scenario = DiscoveryScenario(
        "application_failure",
        "Observe unavailable operation",
        "access_denied",
        "Operation unavailable",
        discovered,
        {},
    )
    failure = FailureResult(
        status="failure",
        run_id="run_" + "e" * 32,
        evidence_manifest="evidence://run_" + "e" * 32 + "/manifest.json",
        code="access_denied",
        message="Operation unavailable",
        recoverable=False,
    )
    assert ReplayValidation(failure).verifies(scenario)
    assert not ReplayValidation(failure, ("unexpected_correction",)).verifies(scenario)
    assert not ReplayValidation(failure.model_copy(update={"code": "target_absent"})).verifies(
        scenario
    )
    assert not successful_validation().verifies(scenario)
