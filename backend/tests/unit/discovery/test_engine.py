from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from replayforge.capabilities.models import CapabilityArtifact, ExtractAction, ObjectContract
from replayforge.discovery.engine import DiscoveryEngine, DiscoveryRequest
from replayforge.discovery.models import (
    ActProposal,
    CompleteProposal,
    DiscoveryProposal,
    DiscoverySuccess,
    EscalateProposal,
    ProviderContext,
    RecordedDiscoveryStep,
)
from replayforge.discovery.ports import ModelProvider, ModelProviderError
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, SurfaceError
from replayforge.surfaces.ports import SurfaceDriver
from tests.unit.replay.test_engine import (
    FakeSurfaceDriver,
    FakeSurfaceSession,
    MemoryInterventionRouter,
    MemoryRecorder,
)


@dataclass
class QueueModelProvider:
    proposals: list[DiscoveryProposal]
    provider_name: str = "test-provider"
    model_name: str = "test-model"
    calls: list[ProviderContext] = field(default_factory=list)

    def decide(self, context: ProviderContext) -> DiscoveryProposal:
        self.calls.append(context)
        return self.proposals.pop(0)


@dataclass
class FailingModelProvider(QueueModelProvider):
    def decide(self, context: ProviderContext) -> DiscoveryProposal:
        del context
        raise ModelProviderError("provider_unavailable", "Provider is temporarily unavailable.")


@dataclass
class ReturningCompiler:
    artifact: CapabilityArtifact
    calls: list[tuple[RecordedDiscoveryStep, ...]] = field(default_factory=list)

    @property
    def output_contract(self) -> ObjectContract:
        return ObjectContract(
            required=("available_balance",),
            properties={"available_balance": self.artifact.outputs.properties["available_balance"]},
        )

    def compile(
        self,
        *,
        run_id: str,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        steps: tuple[RecordedDiscoveryStep, ...],
        final_observation: NormalizedObservation,
        provider_name: str,
        model_name: str,
        evidence_manifest: str,
    ) -> CapabilityArtifact:
        self.calls.append(steps)
        return self.artifact


def build_discovery(
    session: FakeSurfaceSession,
    provider: QueueModelProvider,
    artifact: CapabilityArtifact,
) -> tuple[DiscoveryEngine, ReturningCompiler]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    compiler = ReturningCompiler(artifact)
    policy = EffectivePolicy.intersect(
        PolicyLayer(
            name="test",
            allowed_origins=frozenset({session.origin}),
            allowed_route_patterns=frozenset({"/members/search"}),
            allowed_action_types=frozenset({"type", "click", "extract"}),
            maximum_risk=Risk.READ_ONLY,
        )
    )
    lease_service = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    return (
        DiscoveryEngine(
            surface_driver=cast(SurfaceDriver, FakeSurfaceDriver(session)),
            model_provider=cast(ModelProvider, provider),
            artifact_compiler=compiler,
            policy_evaluator=PolicyEvaluator(clock),
            effective_policy=policy,
            lease_service=lease_service,
            recorder=MemoryRecorder(),
            intervention_router=MemoryInterventionRouter(lease_service),
            clock=clock,
        ),
        compiler,
    )


def make_request(**changes: Any) -> DiscoveryRequest:
    defaults: dict[str, Any] = {
        "run_id": new_id(EntityKind.RUN),
        "goal": "Look up the synthetic member savings balance",
        "application_family": "northstar_member_service",
        "tenant": "harbor_credit_union",
        "entry_point": "member_search",
        "inputs": {"member_id": "12345"},
    }
    defaults.update(changes)
    return DiscoveryRequest(**defaults)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_steps": 0},
        {"max_steps": 51},
        {"timeout": timedelta(seconds=9)},
        {"timeout": timedelta(seconds=601)},
    ],
)
def test_discovery_request_enforces_domain_budget_ceiling(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="discovery"):
        make_request(**changes)


def test_discovery_extraction_transforms_match_artifact_semantics() -> None:
    assert DiscoveryEngine._transform(" Savings ", "lowercase") == "savings"
    assert DiscoveryEngine._transform(" $1,420.57 ", "decimal") == "1420.57"
    assert DiscoveryEngine._transform(" 2026-09-10T12:30:00Z ", "date-time") == (
        "2026-09-10T12:30:00Z"
    )
    assert DiscoveryEngine._transform(" unchanged ", "text") == " unchanged "


def test_successful_loop_records_action_and_compiles_verified_artifact(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    extract_step = artifact.steps[2]
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=extract_step.action,
                target=extract_step.target,
                rationale="The balance is visible and should be captured.",
                expected_effect="Available balance is bound as an output.",
                declared_risk=Risk.READ_ONLY,
                confidence=0.99,
            ),
            CompleteProposal(kind="complete", rationale="The requested balance is visible."),
        ]
    )
    session = FakeSurfaceSession()
    engine, compiler = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, DiscoverySuccess)
    assert result.artifact == artifact
    assert len(compiler.calls) == 1
    assert len(compiler.calls[0]) == 1
    assert provider.calls[0].required_output_names == ("available_balance",)
    assert provider.calls[0].maximum_risk is Risk.READ_ONLY
    assert engine._proposal_summary(CompleteProposal(kind="complete", rationale="Verified.")) == {
        "proposal_kind": "complete"
    }
    assert compiler.calls[0][0].target is not extract_step.target
    assert session.closed is True


def test_low_confidence_escalates_and_preserves_session(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=step.action,
                target=step.target,
                rationale="The target might be correct.",
                expected_effect="Search results may load.",
                declared_risk=Risk.READ_ONLY,
                confidence=0.2,
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, InterventionRequiredResult)
    assert result.code == "low_model_confidence"
    assert session.closed is False


def test_multiple_extractions_from_one_stable_view_are_not_stuck(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    extract_step = artifact.steps[2]
    proposal = ActProposal(
        kind="act",
        action=extract_step.action,
        target=extract_step.target,
        rationale="Extract one declared value from the stable details view.",
        expected_effect="The declared output is bound without changing the page.",
        declared_risk=Risk.READ_ONLY,
        confidence=0.99,
    )
    provider = QueueModelProvider(
        [
            proposal,
            proposal,
            CompleteProposal(kind="complete", rationale="Required output is verified."),
        ]
    )
    session = FakeSurfaceSession(static_fingerprint=True)
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, DiscoverySuccess)
    assert len(provider.calls) == 3


def test_provider_can_explicitly_request_human_help(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [
            EscalateProposal(
                kind="escalate",
                reason_code="unknown_dialog",
                rationale="The dialog is not recognized.",
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, InterventionRequiredResult)
    assert result.code == "unknown_dialog"
    assert session.closed is False


def test_step_budget_stops_unbounded_discovery(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=step.action,
                target=step.target,
                rationale="Submit the search.",
                expected_effect="Search results load.",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request(max_steps=1))

    assert isinstance(result, FailureResult)
    assert result.code == "max_steps_exceeded"
    assert session.closed is True


def test_repeated_observation_escalates_before_looping_forever(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    proposal = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Submit the search.",
        expected_effect="Search results load.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    provider = QueueModelProvider([proposal, proposal])
    session = FakeSurfaceSession(static_fingerprint=True)
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request(max_repeated_state=1))

    assert isinstance(result, InterventionRequiredResult)
    assert result.code == "repeated_observation"
    assert session.closed is False


def test_unverified_completion_is_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [CompleteProposal(kind="complete", rationale="I think the task is done.")]
    )
    session = FakeSurfaceSession(checkpoint_valid=False)
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, FailureResult)
    assert result.code == "completion_not_verified"


def test_provider_failure_becomes_safe_terminal_result(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, FailingModelProvider([]), artifact)

    result = engine.execute(make_request())

    assert isinstance(result, FailureResult)
    assert result.code == "provider_unavailable"
    assert result.message == "Provider is temporarily unavailable."
    assert session.closed is True


def test_recoverable_effect_absent_locator_failure_is_replanned_without_raw_details(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    extract_step = artifact.steps[2]
    proposal = ActProposal(
        kind="act",
        action=extract_step.action,
        target=extract_step.target,
        rationale="Capture the visible balance.",
        expected_effect="Bind the required balance output.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    provider = QueueModelProvider(
        [
            proposal,
            proposal,
            CompleteProposal(kind="complete", rationale="The output is verified."),
        ]
    )
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "target_absent",
            "raw selector diagnostics must not reach the model",
            recoverable=True,
            effect_absent=True,
        ),
        resolve_failures_remaining=1,
    )
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, DiscoverySuccess)
    assert provider.calls[1].action_history == (
        "Previous proposal was not executed (target_absent); choose a different safe target.",
    )
    assert "raw selector diagnostics" not in repr(provider.calls)
    assert isinstance(engine.recorder, MemoryRecorder)
    assert ("proposal_rejected", None) in engine.recorder.events


def test_undeclared_extraction_is_rejected_before_execution(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    extract_step = artifact.steps[2]
    assert isinstance(extract_step.action, ExtractAction)
    valid = ActProposal(
        kind="act",
        action=extract_step.action,
        target=extract_step.target,
        rationale="Capture the declared balance.",
        expected_effect="Bind the required balance output.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    undeclared = valid.model_copy(
        update={"action": extract_step.action.model_copy(update={"output": "status"})}
    )
    provider = QueueModelProvider(
        [undeclared, valid, CompleteProposal(kind="complete", rationale="Verified.")]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, DiscoverySuccess)
    assert provider.calls[1].action_history == (
        "Previous proposal was not executed (output_not_declared); choose a different safe target.",
    )
    assert provider.calls[1].captured_output_names == ()
    assert provider.calls[1].remaining_output_names == ("available_balance",)
    assert isinstance(engine.recorder, MemoryRecorder)
    assert engine.recorder.events.count(("policy_evaluated", None)) == 1
    assert engine.recorder.events.count(("action_intent", None)) == 1


def test_duplicate_extraction_is_rejected_and_captured_state_is_exposed(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    extract_step = artifact.steps[2]
    proposal = ActProposal(
        kind="act",
        action=extract_step.action,
        target=extract_step.target,
        rationale="Capture the declared balance.",
        expected_effect="Bind the required balance output.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    provider = QueueModelProvider(
        [proposal, proposal, CompleteProposal(kind="complete", rationale="Verified.")]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, DiscoverySuccess)
    assert provider.calls[1].captured_output_names == ("available_balance",)
    assert provider.calls[1].remaining_output_names == ()
    assert provider.calls[2].action_history[-1] == (
        "Previous proposal was not executed (output_already_captured); "
        "choose a different safe target."
    )
    assert isinstance(engine.recorder, MemoryRecorder)
    assert engine.recorder.events.count(("policy_evaluated", None)) == 1
    assert engine.recorder.events.count(("action_intent", None)) == 1


def test_nonrecoverable_locator_failure_remains_terminal(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=step.action,
                target=step.target,
                rationale="Submit the search.",
                expected_effect="Show search results.",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            )
        ]
    )
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "target_ambiguous",
            "Multiple controls matched.",
            recoverable=False,
            effect_absent=True,
        ),
        resolve_failures_remaining=1,
    )
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, FailureResult)
    assert result.code == "target_ambiguous"
    assert len(provider.calls) == 1
