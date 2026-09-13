from __future__ import annotations

from datetime import UTC, datetime

import pytest

from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.models import (
    AUTOMATION_OWNER,
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
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock, leases)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    run_id = str(new_id(EntityKind.RUN))
    session_id = new_id(EntityKind.SESSION)
    initial = leases.create_for_automation(str(session_id))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )

    routed_id = router.open(
        intervention_id=intervention_id,
        run_id=run_id,
        session_id=str(session_id),
        expected_lease_version=initial.version,
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


def test_opening_intervention_atomically_pauses_its_control_lease() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock, leases)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    session_id = new_id(EntityKind.SESSION)
    initial = leases.create_for_automation(str(session_id))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )

    router.open(
        intervention_id=intervention_id,
        run_id=str(new_id(EntityKind.RUN)),
        session_id=str(session_id),
        expected_lease_version=initial.version,
        code="dialog_detected",
        step_id="search.submit",
        observation=observation,
        context=context(),
    )

    paused = leases.repository.get(str(session_id))
    assert paused.intervention_id == intervention_id
    assert paused.owner.value == "automation_paused"
    assert router.get(intervention_id).status is InterventionStatus.OPEN

    second_session_id = new_id(EntityKind.SESSION)
    second_initial = leases.create_for_automation(str(second_session_id))
    second_observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=second_session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="second-state",
        landmarks=(),
    )
    with pytest.raises(InterventionConflictError):
        router.open(
            intervention_id=intervention_id,
            run_id=str(new_id(EntityKind.RUN)),
            session_id=str(second_session_id),
            expected_lease_version=second_initial.version,
            code="dialog_detected",
            step_id=None,
            observation=second_observation,
            context=context(),
        )
    assert leases.repository.get(str(second_session_id)).owner == AUTOMATION_OWNER


def test_router_rejects_observation_from_another_session() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock, leases)
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
        router.open(
            intervention_id=str(new_id(EntityKind.INTERVENTION)),
            run_id=str(new_id(EntityKind.RUN)),
            session_id=str(new_id(EntityKind.SESSION)),
            expected_lease_version=1,
            code="stuck",
            step_id=None,
            observation=observation,
            context=context(),
        )


def test_unknown_intervention_is_not_found() -> None:
    clock = FrozenClock(datetime.now(UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock, leases)
    unknown = str(new_id(EntityKind.INTERVENTION))

    with pytest.raises(InterventionNotFoundError):
        router.get(unknown)
    with pytest.raises(InterventionNotFoundError):
        router.observation(unknown)
