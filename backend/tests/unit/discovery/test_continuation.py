"""Blocked discovery continuation using explicit fake surfaces and model proposals."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.discovery.models import (
    ActProposal,
    CompleteProposal,
    DiscoverySuccess,
    EscalateProposal,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import InterventionCoordinator
from replayforge.policy.types import Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult
from replayforge.shared.clock import FrozenClock
from replayforge.surfaces.models import SurfaceError
from tests.unit.discovery.test_engine import QueueModelProvider, build_discovery, make_request
from tests.unit.replay.test_engine import FakeSurfaceSession


def test_blocked_discovery_preserves_session_and_budget_through_human_wait(
    valid_artifact_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_artifact_data["inputs"]["properties"]["member_id"].pop("example", None)
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    step = artifact.steps[2]
    provider = QueueModelProvider(
        [
            EscalateProposal(
                kind="escalate", reason_code="unknown_dialog", rationale="Fixture blocker"
            ),
            ActProposal(
                kind="act",
                action=step.action,
                target=step.target,
                rationale="Read the value",
                expected_effect="Bind the output",
                declared_risk=Risk.READ_ONLY,
                confidence=0.99,
            ),
            CompleteProposal(kind="complete", rationale="Verified output"),
        ]
    )
    session = FakeSurfaceSession(static_fingerprint=True)
    engine, compiler = build_discovery(session, provider, artifact)
    router = InMemoryInterventionRouter(engine.clock, engine.lease_service)
    coordinator = InterventionCoordinator(router, engine.lease_service)
    engine = replace(engine, intervention_router=router)
    result = engine.execute(make_request(max_steps=3, timeout=timedelta(seconds=10)))
    assert isinstance(result, InterventionRequiredResult)
    assert not session.closed
    assert len(provider.calls) == 1
    with pytest.raises(SurfaceError, match="has not changed"):
        engine.validate_resume(result.intervention_id)
    session.route = "/outside-policy"
    with pytest.raises(SurfaceError, match="outside its allowed"):
        engine.validate_resume(result.intervention_id)
    session.route = "/members/search"
    session.static_fingerprint = False
    later = engine.clock.now() + timedelta(hours=1)
    monkeypatch.setattr(FrozenClock, "now", lambda self: later)
    opened = coordinator.get(result.intervention_id)
    claimed = coordinator.claim(result.intervention_id, opened.lease.version, "test-operator")
    started = coordinator.begin_resume(
        result.intervention_id, claimed.lease.version, "test-operator"
    )
    engine.validate_resume(result.intervention_id)
    resumed = coordinator.complete_resume(
        result.intervention_id, started.lease.version, "Changed state"
    )
    completed = engine.resume(result.intervention_id, resumed.lease.version)
    assert isinstance(completed, DiscoverySuccess), completed
    assert len(provider.calls) == 3  # Pause did not restart the model or the contract planner.
    assert len(compiler.calls[0]) == 1  # No invented automation step for human activity.
    assert "human corrected" in provider.calls[1].action_history[-1].lower()
    assert session.closed
    assert not engine._continuations


def test_termination_closes_the_retained_generator(valid_artifact_data: dict[str, Any]) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [
            EscalateProposal(
                kind="escalate", reason_code="unknown_dialog", rationale="Fixture blocker"
            )
        ]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    engine = replace(
        engine, intervention_router=InMemoryInterventionRouter(engine.clock, engine.lease_service)
    )
    paused = engine.execute(make_request())
    assert isinstance(paused, InterventionRequiredResult)
    cancelled = engine.cancel(paused.intervention_id)
    assert isinstance(cancelled, FailureResult)
    assert cancelled.code == "discovery_terminated"
    assert session.closed
    assert not engine._continuations


def test_resume_cannot_reset_the_exhausted_step_budget(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    provider = QueueModelProvider(
        [EscalateProposal(kind="escalate", reason_code="blocked", rationale="Fixture blocker")]
    )
    session = FakeSurfaceSession()
    engine, _ = build_discovery(session, provider, artifact)
    router = InMemoryInterventionRouter(engine.clock, engine.lease_service)
    coordinator = InterventionCoordinator(router, engine.lease_service)
    engine = replace(engine, intervention_router=router)
    paused = engine.execute(make_request(max_steps=1))
    assert isinstance(paused, InterventionRequiredResult)
    opened = coordinator.get(paused.intervention_id)
    claimed = coordinator.claim(paused.intervention_id, opened.lease.version, "test-operator")
    started = coordinator.begin_resume(
        paused.intervention_id, claimed.lease.version, "test-operator"
    )
    engine.validate_resume(paused.intervention_id)
    resumed = coordinator.complete_resume(
        paused.intervention_id, started.lease.version, "Changed state"
    )
    result = engine.resume(paused.intervention_id, resumed.lease.version)
    assert isinstance(result, FailureResult)
    assert result.code == "max_steps_exceeded"
    assert len(provider.calls) == 1
    assert session.closed
    assert not engine._continuations
