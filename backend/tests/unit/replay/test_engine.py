from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from replayforge.capabilities.models import (
    AllCondition,
    CapabilityArtifact,
    Condition,
    LocatorBundle,
    OutputValidCondition,
    RouteCondition,
    TextCondition,
)
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import Risk
from replayforge.replay.engine import ReplayEngine, ReplayRequest
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
    SurfaceError,
    Viewport,
)


@dataclass
class FakeSurfaceSession:
    member_not_found: bool = False
    checkpoint_valid: bool = True
    extraction: str = "$1,420.57"
    resolve_error: SurfaceError | None = None
    resolve_failures_remaining: int = 0
    closed: bool = False
    static_fingerprint: bool = False
    observation_count: int = 0
    session_id: EntityId = field(default_factory=lambda: new_id(EntityKind.SESSION))
    origin: str = "http://demo.local:3001"

    def observe(self) -> NormalizedObservation:
        self.observation_count += 1
        return NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=self.session_id,
            captured_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
            route="/members/search",
            viewport=Viewport(1280, 800),
            fingerprint=(
                "stable-fingerprint"
                if self.static_fingerprint
                else f"state-{self.observation_count}"
            ),
            landmarks=("Member Search",),
        )

    def resolve(self, target: object, timeout_ms: int) -> ResolvedTarget:
        if self.resolve_error is not None and (self.resolve_failures_remaining != 0):
            if self.resolve_failures_remaining > 0:
                self.resolve_failures_remaining -= 1
            raise self.resolve_error
        return ResolvedTarget("fake-handle", "resolved control", 0, 1, Risk.READ_ONLY)

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
        return self.evaluate(condition, outputs, inputs)

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

    def record(
        self,
        event_type: str,
        run_id: str,
        *,
        step_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.events.append((event_type, step_id))


@dataclass
class MemoryInterventionRouter:
    created: list[str] = field(default_factory=list)

    def create(
        self,
        *,
        intervention_id: str,
        run_id: str,
        session_id: str,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
    ) -> str:
        self.created.append(intervention_id)
        return intervention_id


def build_engine(
    session: FakeSurfaceSession,
) -> tuple[ReplayEngine, MemoryRecorder, MemoryInterventionRouter]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    policy = EffectivePolicy.intersect(
        PolicyLayer(
            name="test",
            allowed_origins=frozenset({session.origin}),
            allowed_route_patterns=frozenset({"/members/search", "/accounts/:account_id/details"}),
            allowed_action_types=frozenset({"type", "click", "extract"}),
            maximum_risk=Risk.READ_ONLY,
        )
    )
    recorder = MemoryRecorder()
    router = MemoryInterventionRouter()
    engine = ReplayEngine(
        surface_driver=FakeSurfaceDriver(session),
        policy_evaluator=PolicyEvaluator(clock),
        effective_policy=policy,
        lease_service=ControlLeaseService(InMemoryControlLeaseRepository(), clock),
        recorder=recorder,
        intervention_router=router,
    )
    return engine, recorder, router


def request_for(artifact_data: dict[str, Any], member_id: str = "12345") -> ReplayRequest:
    return ReplayRequest(
        run_id=new_id(EntityKind.RUN),
        artifact=CapabilityArtifact.model_validate(artifact_data),
        tenant="harbor_credit_union",
        inputs={"member_id": member_id},
    )


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


def test_invalid_input_fails_before_opening_surface(
    valid_artifact_data: dict[str, Any],
) -> None:
    session = FakeSurfaceSession()
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data, "bad"))

    assert isinstance(result, FailureResult)
    assert result.code == "invalid_input"
    assert session.closed is False


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
    engine, _, _ = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, FailureResult)
    assert result.code == "checkpoint_mismatch"
    assert not hasattr(result, "outputs")


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
    engine, _, router = build_engine(session)

    result = engine.execute(request_for(valid_artifact_data))

    assert isinstance(result, InterventionRequiredResult)
    assert result.control_owner == "automation_paused"
    assert router.created == [result.intervention_id]
    assert session.closed is False


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
