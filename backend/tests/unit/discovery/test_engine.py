from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from replayforge.capabilities.models import (
    AssertAction,
    CapabilityArtifact,
    Condition,
    ExtractAction,
    IdentityMatchesCondition,
    InputTextCandidate,
    InputValue,
    LiteralValue,
    LocatorBundle,
    ObjectContract,
    OutputValidCondition,
    TextCondition,
    TypeAction,
    WaitForAction,
)
from replayforge.discovery.engine import DiscoveryEngine, DiscoveryRequest
from replayforge.discovery.models import (
    ActProposal,
    CapabilityDraftSpec,
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
from replayforge.policy.types import DataClassification, Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, ResolvedTarget, SurfaceError
from replayforge.surfaces.ports import SurfaceDriver
from tests.unit.replay.test_engine import (
    FakeSurfaceDriver,
    FakeSurfaceSession,
    MemoryInterventionRouter,
    MemoryRecorder,
)


@pytest.fixture
def valid_artifact_data(valid_artifact_data: dict[str, Any]) -> dict[str, Any]:
    # Unlike the hand-authored compatibility fixture, discovery artifacts must
    # not embed the actual invocation value as an example.
    valid_artifact_data["inputs"]["properties"]["member_id"].pop("example", None)
    return valid_artifact_data


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


def test_planned_input_contract_is_checked_before_any_action(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider([])
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    draft = CapabilityDraftSpec(
        operation_slug="read_value",
        name="Read",
        description="Read a value",
        inputs=artifact.inputs,
        outputs=artifact.outputs,
        risk=Risk.READ_ONLY,
    )
    engine = replace(engine, contract_planner=lambda context: draft)
    result = engine.execute(make_request(inputs={"member_id": "not-a-valid-id"}))
    assert isinstance(result, FailureResult)
    assert result.code == "discovery_input_invalid"
    assert provider.calls == []
    assert session.executed_targets == []
    assert session.closed


def test_discovery_grounds_bound_identity_but_records_only_symbolic_target(
    valid_artifact_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    symbolic = LocatorBundle(
        description="Requested record action",
        visual_candidates=(
            InputTextCandidate(
                strategy="input_text",
                value=InputValue(source="input", path="member_id"),
                target_text="Open",
                relation="same_row",
            ),
        ),
    )
    targets: list[LocatorBundle] = []
    original = FakeSurfaceSession.resolve

    def resolve(session: FakeSurfaceSession, target: object, timeout_ms: int) -> ResolvedTarget:
        assert isinstance(target, LocatorBundle)
        targets.append(target)
        return original(session, target, timeout_ms)

    monkeypatch.setattr(FakeSurfaceSession, "resolve", resolve)
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=artifact.steps[1].action,
                target=symbolic,
                rationale="Select requested record",
                expected_effect="Record details",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            ),
            ActProposal(
                kind="act",
                action=artifact.steps[2].action,
                target=artifact.steps[2].target,
                rationale="Read result",
                expected_effect="Output bound",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            ),
            CompleteProposal(kind="complete", rationale="Verified"),
        ]
    )
    literal = LocatorBundle.model_validate(
        {
            "description": "Requested record",
            "visual_candidates": [{"strategy": "rendered_text", "value": "12345"}],
        }
    )
    provider.proposals.insert(0, provider.proposals[0].model_copy(update={"target": literal}))
    engine, compiler = build_discovery(FakeSurfaceSession(), provider, artifact)
    draft = CapabilityDraftSpec(
        operation_slug="read_value",
        name="Read",
        description="Read value",
        inputs=artifact.inputs,
        outputs=artifact.outputs,
        risk=Risk.READ_ONLY,
    )
    engine = replace(engine, contract_planner=lambda context: draft)
    assert isinstance(engine.execute(make_request()), DiscoverySuccess)
    assert '"anchor":"12345"' in targets[0].model_dump_json()
    assert compiler.calls[0][0].target == symbolic
    assert len(targets) == 2
    assert len(compiler.calls[0]) == 2
    assert "12345" not in str(provider.calls[-1].action_history)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("forbidden", [False, True])
def test_discovery_input_classification_applies_before_typing(
    valid_artifact_data: dict[str, Any], nested: bool, forbidden: bool
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    field: dict[str, Any] = {
        "type": "string",
        "description": "Label",
        "data_classification": "personal",
    }
    if nested:
        field = {
            "type": "object",
            "description": "Member",
            "data_classification": "personal",
            "required": ["label"],
            "properties": {
                "label": {"type": "string", "description": "Label", "data_classification": "public"}
            },
        }
    draft = CapabilityDraftSpec(
        operation_slug="read_value",
        name="Read",
        description="Read a value",
        inputs=ObjectContract.model_validate(
            {"required": ["member"], "properties": {"member": field}}
        ),
        outputs=artifact.outputs,
        risk=Risk.READ_ONLY,
    )
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=TypeAction(
                    kind="type",
                    value=InputValue(source="input", path="member.label" if nested else "member"),
                ),
                target=artifact.steps[0].target,
                rationale="Enter the supplied label",
                expected_effect="Field filled",
                declared_risk=Risk.READ_ONLY,
                confidence=0.99,
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    engine = replace(engine, contract_planner=lambda context: draft)
    assert engine.effective_policy is not None
    if forbidden:
        engine = replace(
            engine,
            effective_policy=replace(
                engine.effective_policy,
                forbidden_field_classes=frozenset({DataClassification.PERSONAL}),
            ),
        )
    inputs = {"member": {"label": "Synthetic label"} if nested else "Synthetic label"}
    result = engine.execute(make_request(inputs=inputs, max_steps=1))
    assert isinstance(result, FailureResult)
    assert result.code == ("policy_blocked" if forbidden else "max_steps_exceeded")
    assert bool(session.executed_targets) is not forbidden


def test_discovery_cannot_embed_nested_customer_values_in_literal_actions(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=TypeAction(
                    kind="type", value=LiteralValue(source="literal", value="Synthetic label")
                ),
                target=artifact.steps[0].target,
                rationale="Enter a label",
                expected_effect="Field filled",
                declared_risk=Risk.READ_ONLY,
                confidence=0.99,
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    result = engine.execute(
        make_request(inputs={"member": {"label": "Synthetic label"}}, max_steps=1)
    )
    assert isinstance(result, FailureResult)
    assert result.code == "literal_customer_value"
    assert session.executed_targets == []


def test_missing_discovery_binding_is_rejected_without_dispatch(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [
            ActProposal(
                kind="act",
                action=TypeAction(kind="type", value=InputValue(source="input", path="missing")),
                target=artifact.steps[0].target,
                rationale="Enter the supplied value",
                expected_effect="Field filled",
                declared_risk=Risk.READ_ONLY,
                confidence=0.99,
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    result = engine.execute(make_request(max_steps=1))
    assert isinstance(result, FailureResult)
    assert result.code == "max_steps_exceeded"
    assert session.executed_targets == []
    assert isinstance(engine.recorder, MemoryRecorder)
    assert {
        "code": "input_binding_missing",
        "effect_absent": True,
    } in engine.recorder.recorded_details


def test_publication_privacy_rejection_is_distinct_and_does_not_expose_raw_reason(
    valid_artifact_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from replayforge.evidence.redaction import EvidenceRejectedError

    def reject(*_args: object) -> None:
        raise EvidenceRejectedError("private-value-must-not-be-logged")

    monkeypatch.setattr("replayforge.discovery.engine.validate_artifact_privacy", reject)
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider([CompleteProposal(kind="complete", rationale="Done")])
    engine, _ = build_discovery(FakeSurfaceSession(), provider, artifact)
    result = engine.execute(make_request())
    assert isinstance(result, FailureResult)
    assert result.code == "artifact_privacy_rejected"
    assert "private-value-must-not-be-logged" not in repr(result)
    assert "private-value-must-not-be-logged" not in repr(engine.recorder)


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


def test_ambiguous_retry_remembers_rejected_locator_and_bounds_equivalent_actions(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    proposal = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Submit the search.",
        expected_effect="Show results.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    provider = QueueModelProvider(
        [
            proposal.model_copy(update={"rationale": f"Attempt {index}", "confidence": 0.99})
            for index in range(3)
        ]
    )
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "target_ambiguous",
            "private raw diagnostics",
            recoverable=True,
            effect_absent=True,
        ),
        resolve_failures_remaining=10,
    )
    engine, _ = build_discovery(session, provider, artifact)

    result = engine.execute(make_request())

    assert isinstance(result, InterventionRequiredResult)
    assert result.code == "repeated_action"
    feedback = provider.calls[1].action_history[-1]
    assert "Rejected proposal (not executed):" in feedback
    assert '"target":' in feedback
    assert "Both the anchor and the related target must be unique" in feedback
    assert "private raw diagnostics" not in repr(provider.calls)
    assert len(provider.calls) == 3


def test_operation_fingerprint_ignores_explanation_but_preserves_target_changes(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[1]
    assert step.target is not None
    proposal = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Search.",
        expected_effect="Results.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    reworded = proposal.model_copy(
        update={
            "rationale": "Try again.",
            "expected_effect": "Show results.",
            "confidence": 0.99,
            "target": step.target.model_copy(update={"description": "A different explanation"}),
        }
    )
    assert DiscoveryEngine._operation_fingerprint(proposal) == (
        DiscoveryEngine._operation_fingerprint(reworded)
    )
    assert DiscoveryEngine._operation_fingerprint(proposal) != (
        DiscoveryEngine._operation_fingerprint(proposal.model_copy(update={"target": None}))
    )


@pytest.mark.parametrize("kind", ["assert", "wait_for"])
def test_condition_before_extraction_is_replanned_without_recording_a_false_assertion(
    valid_artifact_data: dict[str, Any],
    kind: str,
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[2]
    condition = OutputValidCondition(kind="output_valid", output="available_balance")
    action = (
        AssertAction(kind="assert", condition=condition)
        if kind == "assert"
        else (WaitForAction(kind="wait_for", condition=condition))
    )
    check = ActProposal(
        kind="act",
        action=action,
        target=None,
        rationale="Verify output.",
        expected_effect="Output is bound.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    extract = check.model_copy(update={"action": step.action, "target": step.target})
    provider = QueueModelProvider(
        [
            check,
            extract,
            check,
            CompleteProposal(kind="complete", rationale="Verified."),
        ]
    )
    session = FakeSurfaceSession()
    engine, compiler = build_discovery(session, provider, artifact)
    assert engine.effective_policy is not None
    engine = replace(
        engine,
        effective_policy=replace(
            engine.effective_policy,
            allowed_action_types=engine.effective_policy.allowed_action_types | {kind},
        ),
    )
    result = engine.execute(make_request())
    assert isinstance(result, DiscoverySuccess)
    assert "condition_output_unbound" in provider.calls[1].action_history[-1]
    assert "Seeing text on screen does not bind an output" in provider.calls[1].action_history[-1]
    assert len(compiler.calls[0]) == 2
    assert isinstance(compiler.calls[0][0].action, ExtractAction)
    assert isinstance(engine.recorder, MemoryRecorder)
    assert engine.recorder.events.count(("action_intent", None)) == 2


@pytest.mark.parametrize("repeat", [False, True])
def test_verified_conditions_allow_static_screens_but_not_unbounded_repetition(
    valid_artifact_data: dict[str, Any],
    repeat: bool,
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[2]
    extract = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Read output.",
        expected_effect="Output bound.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    check = extract.model_copy(
        update={
            "target": None,
            "action": AssertAction(
                kind="assert",
                condition=OutputValidCondition(kind="output_valid", output="available_balance"),
            ),
        }
    )
    distinct = check.model_copy(
        update={
            "action": AssertAction(
                kind="assert",
                condition=TextCondition(kind="text", value="Ready"),
            )
        }
    )
    proposals: list[DiscoveryProposal] = (
        [extract, check, check, check]
        if repeat
        else [extract, check, distinct, CompleteProposal(kind="complete", rationale="Verified.")]
    )
    provider = QueueModelProvider(proposals)
    engine, _ = build_discovery(FakeSurfaceSession(static_fingerprint=True), provider, artifact)
    assert engine.effective_policy is not None
    engine = replace(
        engine,
        effective_policy=replace(
            engine.effective_policy,
            allowed_action_types=engine.effective_policy.allowed_action_types | {"assert"},
        ),
    )
    result = engine.execute(make_request(max_repeated_state=1))
    if repeat:
        assert isinstance(result, InterventionRequiredResult)
        assert result.code == "repeated_action"
    else:
        assert isinstance(result, DiscoverySuccess)
    assert "Condition verified and retained" in provider.calls[2].action_history[-1]


def test_bound_identity_mismatch_remains_a_terminal_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    class ComparingSession(FakeSurfaceSession):
        def evaluate(
            self, condition: Condition, outputs: dict[str, Any], inputs: dict[str, Any]
        ) -> bool:
            if isinstance(condition, IdentityMatchesCondition):
                return outputs.get(condition.extracted_output) == inputs.get(condition.input_path)
            return super().evaluate(condition, outputs, inputs)

    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[2]
    extract = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Extract output.",
        expected_effect="Output bound.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    check = extract.model_copy(
        update={
            "target": None,
            "action": AssertAction(
                kind="assert",
                condition=IdentityMatchesCondition(
                    kind="identity_matches",
                    extracted_output="available_balance",
                    input_path="member_id",
                ),
            ),
        }
    )
    provider = QueueModelProvider([extract, check])
    engine, compiler = build_discovery(ComparingSession(), provider, artifact)
    assert engine.effective_policy is not None
    engine = replace(
        engine,
        effective_policy=replace(
            engine.effective_policy,
            allowed_action_types=engine.effective_policy.allowed_action_types | {"assert"},
        ),
    )
    result = engine.execute(make_request())
    assert isinstance(result, FailureResult)
    assert result.code == "action_condition_not_verified"
    assert compiler.calls == []


def test_value_bound_extraction_is_replanned_without_binding_or_recording_it(
    valid_artifact_data: dict[str, Any],
) -> None:
    class CapturingSession(FakeSurfaceSession):
        latest: LocatorBundle | None = None

        def resolve(self, target: object, timeout_ms: int) -> ResolvedTarget:
            assert isinstance(target, LocatorBundle)
            self.latest = target
            return super().resolve(target, timeout_ms)

        def capture_locator(self, target: ResolvedTarget) -> LocatorBundle:
            assert self.latest is not None
            return self.latest

    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[2]
    good = ActProposal(
        kind="act",
        action=step.action,
        target=step.target,
        rationale="Capture the declared output.",
        expected_effect="Bind output.",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
    )
    bad = good.model_copy(
        update={
            "target": LocatorBundle.model_validate(
                {
                    "description": "Displayed amount",
                    "visual_candidates": [
                        {
                            "strategy": "ocr_relative",
                            "anchor": "Available balance",
                            "target_text": "$1,420.57",
                            "relation": "right_of",
                        }
                    ],
                }
            )
        }
    )
    provider = QueueModelProvider(
        [bad, good, CompleteProposal(kind="complete", rationale="Verified.")]
    )
    engine, compiler = build_discovery(CapturingSession(), provider, artifact)
    result = engine.execute(make_request())
    assert isinstance(result, DiscoverySuccess)
    assert provider.calls[1].captured_output_names == ()
    assert "locator contains the value" in provider.calls[1].action_history[-1]
    assert len(compiler.calls[0]) == 1
    assert compiler.calls[0][0].target == step.target


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


def test_output_can_be_recaptured_and_captured_state_is_exposed(
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
    assert "not executed" not in provider.calls[2].action_history[-1]
    assert isinstance(engine.recorder, MemoryRecorder)
    assert engine.recorder.events.count(("policy_evaluated", None)) == 2
    assert engine.recorder.events.count(("action_intent", None)) == 2


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
