from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    CapabilityArtifact,
    Condition,
    LocatorBundle,
    OutputValidCondition,
    RouteCondition,
    TextCondition,
)
from replayforge.evidence.models import EvidenceRecord, RetentionClass, SanitizedEvidence
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.models import InterventionContext
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import Risk
from replayforge.replay.engine import (
    ReplayContinuation,
    ReplayEngine,
    ReplayRequest,
    ResumeValidationError,
)
from replayforge.runs.results import (
    BusinessOutcomeResult,
    FailureResult,
    InterventionRequiredResult,
    SuccessResult,
)
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityId, EntityKind, new_id
from replayforge.surfaces.models import (
    ActionReceipt,
    ActionStatus,
    NormalizedObservation,
    ResolvedTarget,
    SanitizedSurfaceFrame,
    SurfaceError,
    Viewport,
)
from replayforge.surfaces.ports import SurfaceDriver, SurfaceSession


@dataclass
class FakeSurfaceSession:
    member_not_found: bool = False
    member_not_found_after_wait: bool = False
    checkpoint_valid: bool = True
    extraction: str = "$1,420.57"
    resolve_error: SurfaceError | None = None
    resolve_failures_remaining: int = 0
    closed: bool = False
    static_fingerprint: bool = False
    observation_count: int = 0
    route: str = "/members/search"
    postconditions_valid: bool = True
    evidence_capture_error: SurfaceError | None = None
    interstitial_visible: bool = False
    recovery_clears_interstitial: bool = True
    recovery_registered_risk: Risk = Risk.READ_ONLY
    permission_denied: bool = False
    executed_targets: list[str] = field(default_factory=list)
    session_id: EntityId = field(default_factory=lambda: new_id(EntityKind.SESSION))
    origin: str = "http://demo.local:3001"

    def observe(self) -> NormalizedObservation:
        self.observation_count += 1
        return NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=self.session_id,
            captured_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
            route=self.route,
            viewport=Viewport(1280, 800),
            fingerprint=(
                "stable-fingerprint"
                if self.static_fingerprint
                else f"state-{self.observation_count}"
            ),
            landmarks=("Member Search",),
        )

    def capture_provider_frame(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nsynthetic-frame"

    def capture_sanitized_evidence_frame(self) -> SanitizedSurfaceFrame:
        if self.evidence_capture_error is not None:
            raise self.evidence_capture_error
        return SanitizedSurfaceFrame(
            b"\x89PNG\r\n\x1a\nmasked-synthetic-frame",
            ("mask:synthetic-fields",),
        )

    def resolve(self, target: object, timeout_ms: int) -> ResolvedTarget:
        if self.resolve_error is not None and (self.resolve_failures_remaining != 0):
            if self.resolve_failures_remaining > 0:
                self.resolve_failures_remaining -= 1
            raise self.resolve_error
        description = (
            target.description if isinstance(target, LocatorBundle) else "resolved control"
        )
        registered_risk = (
            self.recovery_registered_risk if description == "Continue notice" else Risk.READ_ONLY
        )
        return ResolvedTarget("fake-handle", description, 0, 1, registered_risk)

    def capture_locator(self, target: ResolvedTarget) -> LocatorBundle:
        return LocatorBundle.model_validate(
            {
                "description": target.description,
                "candidates": [{"strategy": "role_name", "role": "button", "name": "Resolved"}],
            }
        )

    def execute(
        self, action: object, target: ResolvedTarget | None, inputs: dict[str, Any]
    ) -> ActionReceipt:
        if target is not None:
            self.executed_targets.append(target.description)
            if target.description == "Continue notice" and self.recovery_clears_interstitial:
                self.interstitial_visible = False
        now = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
        return ActionReceipt(ActionStatus.COMPLETED, now, now, "visible state changed")

    def evaluate(
        self, condition: Condition, outputs: dict[str, Any], inputs: dict[str, Any]
    ) -> bool:
        if isinstance(condition, AllCondition):
            is_final_checkpoint = any(
                isinstance(item, RouteCondition) and item.pattern == "/accounts/*/details"
                for item in condition.conditions
            )
            return (not is_final_checkpoint or self.checkpoint_valid) and all(
                self.evaluate(item, outputs, inputs) for item in condition.conditions
            )
        if isinstance(condition, OutputValidCondition):
            return condition.output in outputs
        if isinstance(condition, TextCondition) and condition.value == "No member found":
            return self.member_not_found
        if isinstance(condition, TextCondition) and condition.value == "Member Results":
            return self.postconditions_valid and not self.interstitial_visible
        if isinstance(condition, TextCondition) and condition.value == "Important notice":
            return self.interstitial_visible
        if isinstance(condition, TextCondition) and condition.value == "Permission denied":
            return self.permission_denied
        return True

    def extract(self, target: ResolvedTarget) -> str:
        return self.extraction

    def wait_until(
        self,
        condition: Condition,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> bool:
        satisfied = self.evaluate(condition, outputs, inputs)
        if self.member_not_found_after_wait:
            self.member_not_found = True
        return satisfied

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeSurfaceDriver:
    session: FakeSurfaceSession

    def open(self, application_family: str, tenant: str, entry_point: str) -> FakeSurfaceSession:
        return self.session


@dataclass
class MemoryRecorder:
    evidence_manifest_key: str = "evidence://test/manifest.json"
    events: list[tuple[str, str | None]] = field(default_factory=list)
    attachments: list[tuple[str, SanitizedEvidence, RetentionClass]] = field(default_factory=list)
    recorded_details: list[dict[str, object]] = field(default_factory=list)

    def record(
        self,
        event_type: str,
        run_id: str,
        *,
        step_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.events.append((event_type, step_id))
        self.recorded_details.append(details or {})

    def attach_sanitized(
        self,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord:
        self.attachments.append((kind, payload, retention_class))
        return EvidenceRecord(
            id=new_id(EntityKind.EVIDENCE),
            key=f"evidence://test/{kind}.png",
            media_type=payload.media_type,
            size_bytes=len(payload.content),
            content_hash="sha256:" + "0" * 64,
            retention_class=retention_class,
            redaction_directives=payload.redaction_directives,
            created_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
        )


@dataclass
class MemoryInterventionRouter:
    created: list[str] = field(default_factory=list)
    contexts: list[InterventionContext | None] = field(default_factory=list)

    def create(
        self,
        *,
        intervention_id: str,
        run_id: str,
        session_id: str,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
        context: InterventionContext | None = None,
        explanation: str | None = None,
    ) -> str:
        del run_id, session_id, code, step_id, observation, explanation
        self.created.append(intervention_id)
        self.contexts.append(context)
        return intervention_id


def build_engine(
    session: FakeSurfaceSession,
    *,
    maximum_risk: Risk = Risk.READ_ONLY,
    continuation_sink: list[ReplayContinuation] | None = None,
) -> tuple[ReplayEngine, MemoryRecorder, MemoryInterventionRouter]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    policy = EffectivePolicy.intersect(
        PolicyLayer(
            name="test",
            allowed_origins=frozenset({session.origin}),
            allowed_route_patterns=frozenset({"/members/search", "/accounts/:account_id/details"}),
            allowed_action_types=frozenset({"type", "click", "extract"}),
            maximum_risk=maximum_risk,
        )
    )
    recorder = MemoryRecorder()
    router = MemoryInterventionRouter()
    engine = ReplayEngine(
        surface_driver=cast(SurfaceDriver, FakeSurfaceDriver(session)),
        policy_evaluator=PolicyEvaluator(clock),
        effective_policy=policy,
        lease_service=ControlLeaseService(InMemoryControlLeaseRepository(), clock),
        recorder=recorder,
        intervention_router=router,
        continuation_sink=(continuation_sink.append if continuation_sink is not None else None),
    )
    return engine, recorder, router


def test_extraction_transforms_are_canonical() -> None:
    assert ReplayEngine._transform(" Savings ", "lowercase") == "savings"
    assert ReplayEngine._transform(" $1,420.57 ", "decimal") == "1420.57"
    assert ReplayEngine._transform(" unchanged ", "text") == " unchanged "


def request_for(artifact_data: dict[str, Any], member_id: str = "12345") -> ReplayRequest:
    return ReplayRequest(
        run_id=new_id(EntityKind.RUN),
        artifact=CapabilityArtifact.model_validate(artifact_data),
        tenant="harbor_credit_union",
        inputs={"member_id": member_id},
    )


def add_interstitial_recovery(
    artifact_data: dict[str, Any], *, verify_recovery_effect: bool = True
) -> None:
    artifact_data["steps"][1]["recovery_refs"] = ["dismiss_notice"]
    artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    recovery_step: dict[str, Any] = {
        "id": "recovery.dismiss_notice",
        "name": "Dismiss known notice",
        "action": {"kind": "click"},
        "target": {
            "description": "Continue notice",
            "candidates": [
                {
                    "strategy": "role_name",
                    "role": "link",
                    "name": "Continue",
                }
            ],
        },
        "risk": "read_only",
    }
    if verify_recovery_effect:
        recovery_step["postconditions"] = [
            {"kind": "text", "value": "Member Results", "match": "exact"}
        ]
    artifact_data["recoveries"] = [
        {
            "id": "dismiss_notice",
            "trigger": {"kind": "text", "value": "Important notice", "match": "exact"},
            "max_uses": 1,
            "steps": [recovery_step],
            "resume_at": "account.extract_balance",
        }
    ]


def add_permission_failure(artifact_data: dict[str, Any]) -> None:
    artifact_data["steps"][1]["failure_refs"] = ["permission_denied"]
    artifact_data["failures"] = [
        {
            "code": "permission_denied",
            "description": "The current role cannot view the requested member.",
            "detect": {
                "kind": "text",
                "value": "Permission denied",
                "match": "exact",
            },
            "allowed_after_steps": ["search.submit"],
            "expected_state": "member_results",
            "observed_state": "permission_denied",
            "recoverable": False,
        }
    ]


def test_success_is_model_free_and_checkpoint_verified(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession()
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, SuccessResult)
    assert result.outputs == {"available_balance": "1420.57"}
    assert result.checkpoint.verified is True
    assert session.closed is True
    assert ("checkpoint_verified", None) in recorder.events


def test_known_not_found_is_business_outcome_before_missing_happy_path(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(member_not_found=True)
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data, "123456789"))

    assert isinstance(result, BusinessOutcomeResult)
    assert result.code == "member_not_found"
    assert result.details == {"member_id": "***6789"}
    assert session.closed is True


def test_known_outcome_is_rechecked_after_waiting_for_ui_transition(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    session = FakeSurfaceSession(member_not_found_after_wait=True)
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data, "123456789"))

    assert isinstance(result, BusinessOutcomeResult)
    assert result.code == "member_not_found"
    assert result.details == {"member_id": "***6789"}


def test_invalid_input_fails_before_opening_surface(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession()
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data, "bad"))

    assert isinstance(result, FailureResult)
    assert result.code == "invalid_input"
    assert session.closed is False
    assert recorder.attachments == []
    assert recorder.recorded_details[-1]["evidence_frame"] == "not_applicable"


def test_incompatible_tenant_fails_before_opening_surface(
    valid_artifact_data: dict[str, Any],
) -> None:
    request = request_for(valid_artifact_data)
    request = ReplayRequest(request.run_id, request.artifact, "other_tenant", request.inputs)
    session = FakeSurfaceSession()
    engine, _, _ = build_engine(session)

    result = engine.execute(request)

    assert isinstance(result, FailureResult)
    assert result.code == "incompatible_tenant"
    assert session.closed is False


def test_checkpoint_mismatch_never_returns_outputs(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(checkpoint_valid=False)
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "checkpoint_mismatch"
    assert not hasattr(result, "outputs")
    assert [kind for kind, _, _ in recorder.attachments] == ["failure-state"]
    assert recorder.attachments[0][2] is RetentionClass.FAILURE
    assert recorder.recorded_details[-1]["evidence_frame"] == "captured"


def test_failure_capture_error_preserves_original_typed_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(
        checkpoint_valid=False,
        evidence_capture_error=SurfaceError(
            "evidence_screenshot_failed",
            "A sanitized evidence frame could not be captured.",
        ),
    )
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "checkpoint_mismatch"
    assert recorder.attachments == []
    assert recorder.recorded_details[-1]["evidence_frame"] == "unavailable"


def test_target_ambiguity_returns_step_debug_context(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "target_ambiguous",
            "Expected one target and observed two.",
            expected={"count": 1},
            observed={"count": 2},
        ),
        resolve_failures_remaining=-1,
    )
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "target_ambiguous"
    assert result.step_id == "search.enter_member_id"
    assert result.expected == {"count": 1}
    assert result.observed == {"count": 2}


def test_unexpected_dialog_preserves_session_for_intervention(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "unexpected_dialog",
            "An unknown dialog blocks progress.",
            intervention_recommended=True,
        ),
        resolve_failures_remaining=-1,
    )
    engine, recorder, router = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, InterventionRequiredResult)
    assert result.control_owner == "automation_paused"
    assert router.created == [result.intervention_id]
    assert [kind for kind, _, _ in recorder.attachments] == ["handoff-before"]
    assert session.closed is False


def test_replay_continuation_revalidates_and_finishes_without_replaying_human_step(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    valid_artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    session = FakeSurfaceSession()
    continuations: list[ReplayContinuation] = []
    engine, recorder, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )
    request = request_for(valid_artifact_data)

    paused_result = engine.execute(request)

    assert isinstance(paused_result, InterventionRequiredResult)
    assert len(continuations) == 1
    continuation = continuations[0]
    paused_lease = engine.lease_service.repository.get(str(session.session_id))
    claimed = engine.lease_service.claim(
        str(session.session_id),
        paused_lease.version,
        paused_result.intervention_id,
        "operator-7",
    )
    returned = engine.lease_service.begin_resume(
        str(session.session_id), claimed.version, "operator-7"
    )
    outcome = engine.validate_resume(continuation)
    automation = engine.lease_service.complete_resume(str(session.session_id), returned.version)

    resumed_result = engine.resume(continuation, automation.version, outcome)

    assert isinstance(resumed_result, SuccessResult)
    assert resumed_result.outputs == {"available_balance": "1420.57"}
    assert session.closed is True
    assert ("resume_revalidation_started", "search.submit") in recorder.events
    assert ("resume_checkpoint_verified", "search.submit") in recorder.events
    assert [kind for kind, _, _ in recorder.attachments] == [
        "handoff-before",
        "handoff-after",
    ]
    assert all(
        attachment.redaction_directives == ("mask:synthetic-fields",)
        for _, attachment, _ in recorder.attachments
    )


def test_replay_continuation_rejects_unchanged_human_state(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    valid_artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    session = FakeSurfaceSession(static_fingerprint=True)
    continuations: list[ReplayContinuation] = []
    engine, _, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )

    assert isinstance(engine.execute(request_for(valid_artifact_data)), InterventionRequiredResult)

    with pytest.raises(ResumeValidationError, match="has not changed"):
        engine.validate_resume(continuations[0])
    assert session.closed is False


def test_replay_continuation_returns_human_discovered_business_outcome(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    session = FakeSurfaceSession()
    continuations: list[ReplayContinuation] = []
    engine, recorder, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )
    paused_result = engine.execute(request_for(valid_artifact_data, "123456789"))
    assert isinstance(paused_result, InterventionRequiredResult)
    session.member_not_found = True

    outcome = engine.validate_resume(continuations[0])
    resumed_result = engine.resume(continuations[0], automation_lease_version=5, outcome=outcome)

    assert isinstance(resumed_result, BusinessOutcomeResult)
    assert resumed_result.code == "member_not_found"
    assert resumed_result.details == {"member_id": "***6789"}
    assert session.closed is True
    assert ("resume_checkpoint_verified", "search.submit") in recorder.events
    assert [kind for kind, _, _ in recorder.attachments] == [
        "handoff-before",
        "handoff-after",
    ]


def test_replay_continuation_rejects_disallowed_location(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    valid_artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    session = FakeSurfaceSession()
    continuations: list[ReplayContinuation] = []
    engine, _, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )
    assert isinstance(engine.execute(request_for(valid_artifact_data)), InterventionRequiredResult)
    session.origin = "https://outside.example"

    with pytest.raises(ResumeValidationError, match="outside") as captured:
        engine.validate_resume(continuations[0])

    assert captured.value.code == "resume_location_not_allowed"


def test_replay_continuation_requires_declared_postcondition(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    session = FakeSurfaceSession()
    continuations: list[ReplayContinuation] = []
    engine, _, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )
    assert isinstance(engine.execute(request_for(valid_artifact_data)), InterventionRequiredResult)

    with pytest.raises(ResumeValidationError, match="no declared postcondition") as captured:
        engine.validate_resume(continuations[0])

    assert captured.value.code == "resume_checkpoint_missing"


def test_replay_continuation_rejects_postcondition_mismatch(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][1]["risk"] = "sensitive"
    valid_artifact_data["steps"][1]["postconditions"] = [
        {"kind": "text", "value": "Member Results", "match": "exact"}
    ]
    session = FakeSurfaceSession()
    continuations: list[ReplayContinuation] = []
    engine, _, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )
    assert isinstance(engine.execute(request_for(valid_artifact_data)), InterventionRequiredResult)
    session.postconditions_valid = False

    with pytest.raises(ResumeValidationError, match="does not satisfy") as captured:
        engine.validate_resume(continuations[0])

    assert captured.value.code == "resume_checkpoint_mismatch"


def test_replay_continuation_validates_index(valid_artifact_data: dict[str, Any]) -> None:
    session = FakeSurfaceSession()
    engine, _, _ = build_engine(session)
    invalid = ReplayContinuation(
        intervention_id=str(new_id(EntityKind.INTERVENTION)),
        request=request_for(valid_artifact_data),
        session=cast(SurfaceSession, session),
        inputs={"member_id": "12345"},
        outputs={},
        recovery_uses={},
        interrupted_step_index=999,
        initial_fingerprint="state",
    )

    with pytest.raises(ValueError, match="outside the artifact"):
        engine.validate_resume(invalid)


def test_invalid_extracted_output_is_a_typed_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession(extraction="not money")
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "output_validation_failed"


def test_declared_recoverable_absent_target_retries_once(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["steps"][0]["retry"] = {
        "max_attempts": 2,
        "backoff_ms": [0],
        "retry_on": ["target_temporarily_absent"],
        "require_effect_absent": True,
    }
    session = FakeSurfaceSession(
        resolve_error=SurfaceError(
            "target_temporarily_absent",
            "Target has not rendered yet.",
            recoverable=True,
            effect_absent=True,
        ),
        resolve_failures_remaining=1,
    )
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, SuccessResult)
    assert ("step_retry_scheduled", "search.enter_member_id") in recorder.events


def test_declared_application_failure_returns_debuggable_typed_result(
    valid_artifact_data: dict[str, Any],
) -> None:
    add_permission_failure(valid_artifact_data)
    session = FakeSurfaceSession(permission_denied=True)
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "permission_denied"
    assert result.step_id == "search.submit"
    assert result.expected == {"state": "member_results"}
    assert result.observed == {"state": "permission_denied"}
    assert result.recoverable is False
    assert [kind for kind, _, _ in recorder.attachments] == ["failure-state"]


def test_declared_recovery_executes_once_and_resumes_at_named_step(
    valid_artifact_data: dict[str, Any],
) -> None:
    add_interstitial_recovery(valid_artifact_data)
    session = FakeSurfaceSession(interstitial_visible=True)
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, SuccessResult)
    assert session.executed_targets.count("Search button") == 1
    assert session.executed_targets.count("Continue notice") == 1
    assert ("recovery_started", "search.submit") in recorder.events
    assert ("recovery_completed", "search.submit") in recorder.events
    recovery_details = [
        details
        for event, details in zip(recorder.events, recorder.recorded_details, strict=True)
        if event[0].startswith("recovery_")
    ]
    assert recovery_details == [
        {"recovery_id": "dismiss_notice", "use": 1},
        {"recovery_id": "dismiss_notice", "resume_at": "account.extract_balance"},
    ]


def test_declared_recovery_exhaustion_is_a_typed_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    add_interstitial_recovery(valid_artifact_data, verify_recovery_effect=False)
    valid_artifact_data["recoveries"][0]["resume_at"] = "search.submit"
    session = FakeSurfaceSession(
        interstitial_visible=True,
        recovery_clears_interstitial=False,
    )
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "recovery_exhausted"
    assert session.executed_targets.count("Continue notice") == 1
    assert ("recovery_exhausted", "search.submit") in recorder.events
    assert [kind for kind, _, _ in recorder.attachments] == ["failure-state"]


def test_unmatched_recovery_trigger_preserves_original_failure(
    valid_artifact_data: dict[str, Any],
) -> None:
    add_interstitial_recovery(valid_artifact_data)
    session = FakeSurfaceSession(postconditions_valid=False)
    engine, recorder, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "postcondition_mismatch"
    assert not any(event.startswith("recovery_") for event, _ in recorder.events)


def test_runtime_risk_escalation_cannot_pause_inside_recovery(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    add_interstitial_recovery(valid_artifact_data)
    session = FakeSurfaceSession(
        interstitial_visible=True,
        recovery_registered_risk=Risk.SENSITIVE,
    )
    engine, _, router = build_engine(session, maximum_risk=Risk.SENSITIVE)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "recovery_requires_human"
    assert router.created == []
    assert session.closed is True


def test_recovery_budget_is_retained_across_later_human_handoff(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][2]["risk"] = "sensitive"
    add_interstitial_recovery(valid_artifact_data)
    session = FakeSurfaceSession(interstitial_visible=True)
    continuations: list[ReplayContinuation] = []
    engine, _, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, InterventionRequiredResult)
    assert continuations[0].interrupted_step_index == 2
    assert continuations[0].recovery_uses == {"dismiss_notice": 1}
    assert session.closed is False


def test_recovery_control_flow_also_applies_after_validated_resume(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["risk"] = "sensitive"
    valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
    valid_artifact_data["steps"][0]["risk"] = "sensitive"
    valid_artifact_data["steps"][0]["postconditions"] = [
        {"kind": "text", "value": "Member Search", "match": "exact"}
    ]
    add_interstitial_recovery(valid_artifact_data)
    session = FakeSurfaceSession(interstitial_visible=True)
    continuations: list[ReplayContinuation] = []
    engine, recorder, _ = build_engine(
        session,
        maximum_risk=Risk.SENSITIVE,
        continuation_sink=continuations,
    )

    paused = engine.execute(request_for(valid_artifact_data))
    assert isinstance(paused, InterventionRequiredResult)
    paused_lease = engine.lease_service.repository.get(str(session.session_id))
    claimed = engine.lease_service.claim(
        str(session.session_id), paused_lease.version, paused.intervention_id, "operator-7"
    )
    returned = engine.lease_service.begin_resume(
        str(session.session_id), claimed.version, "operator-7"
    )
    outcome = engine.validate_resume(continuations[0])
    automation = engine.lease_service.complete_resume(str(session.session_id), returned.version)

    result = engine.resume(continuations[0], automation.version, outcome)

    assert isinstance(result, SuccessResult)
    assert session.executed_targets.count("Continue notice") == 1
    assert ("recovery_completed", "search.submit") in recorder.events
