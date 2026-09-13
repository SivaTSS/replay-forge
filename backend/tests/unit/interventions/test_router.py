from __future__ import annotations

from datetime import UTC, datetime

import pytest

from replayforge.interventions.models import (
    InterventionContext,
    InterventionRunMode,
    InterventionStatus,
)
from replayforge.interventions.router import (
    InMemoryInterventionRouter,
    InterventionConflictError,
    InterventionNotFoundError,
)
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


def context() -> InterventionContext:
    return InterventionContext(
        run_mode=InterventionRunMode.REPLAY,
        application_family="northstar",
        tenant="harbor",
        task_summary="Lookup balance",
        surface_route="/members/search",
        capability_id="member.lookup",
        capability_version="1.0.0",
        capability_name="Lookup",
    )


def test_router_preserves_reserved_identity_and_observation() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    router = InMemoryInterventionRouter(clock)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    run_id = str(new_id(EntityKind.RUN))
    session_id = new_id(EntityKind.SESSION)
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )

    routed_id = router.create(
        intervention_id=intervention_id,
        run_id=run_id,
        session_id=str(session_id),
        code="dialog_detected",
        step_id="search.submit",
        observation=observation,
        context=context(),
    )

    assert routed_id == intervention_id
    assert router.get(intervention_id).status is InterventionStatus.OPEN
    assert router.observation(intervention_id) is observation
    assert router.list_open() == (router.get(intervention_id),)
    assert router.list_active(InterventionRunMode.REPLAY) == (router.get(intervention_id),)
    assert router.list_active(InterventionRunMode.DISCOVERY) == ()

    with pytest.raises(InterventionConflictError):
        router.create(
            intervention_id=intervention_id,
            run_id=run_id,
            session_id=str(session_id),
            code="dialog_detected",
            step_id=None,
            observation=observation,
            context=context(),
        )


def test_router_rejects_observation_from_another_session() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    router = InMemoryInterventionRouter(clock)
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )

    with pytest.raises(ValueError, match="does not belong"):
        router.create(
            intervention_id=str(new_id(EntityKind.INTERVENTION)),
            run_id=str(new_id(EntityKind.RUN)),
            session_id=str(new_id(EntityKind.SESSION)),
            code="stuck",
            step_id=None,
            observation=observation,
            context=context(),
        )


def test_unknown_intervention_is_not_found() -> None:
    router = InMemoryInterventionRouter(FrozenClock(datetime.now(UTC)))
    unknown = str(new_id(EntityKind.INTERVENTION))

    with pytest.raises(InterventionNotFoundError):
        router.get(unknown)
    with pytest.raises(InterventionNotFoundError):
        router.observation(unknown)


def test_compare_and_swap_rejects_stale_status_and_identity_change() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    router = InMemoryInterventionRouter(clock)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    run_id = str(new_id(EntityKind.RUN))
    session_id = new_id(EntityKind.SESSION)
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )
    router.create(
        intervention_id=intervention_id,
        run_id=run_id,
        session_id=str(session_id),
        code="stuck",
        step_id=None,
        observation=observation,
        context=context(),
    )
    current = router.get(intervention_id)

    with pytest.raises(InterventionConflictError, match="stale"):
        router.compare_and_swap(intervention_id, InterventionStatus.CLAIMED, current)
    with pytest.raises(ValueError, match="immutable"):
        router.compare_and_swap(
            intervention_id,
            InterventionStatus.OPEN,
            current.__class__(
                id=new_id(EntityKind.INTERVENTION),
                run_id=current.run_id,
                session_id=current.session_id,
                trigger_code=current.trigger_code,
                explanation=current.explanation,
                status=current.status,
                created_at=current.created_at,
                context=current.context,
            ),
        )
