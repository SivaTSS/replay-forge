from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
    LeaseConflictError,
)
from replayforge.interventions.models import (
    AUTOMATION_OWNER,
    InterventionContext,
    InterventionRunMode,
    InterventionStatus,
    OwnerKind,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import InterventionCoordinator, InterventionTransition
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


def coordinator() -> tuple[InterventionCoordinator, str]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock)
    session_id = str(new_id(EntityKind.SESSION))
    lease = leases.create_for_automation(session_id)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    paused = leases.pause(session_id, lease.version, intervention_id)
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=paused.session_id,
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="state",
        landmarks=(),
    )
    router.create(
        intervention_id=intervention_id,
        run_id=str(new_id(EntityKind.RUN)),
        session_id=session_id,
        code="stuck",
        step_id=None,
        observation=observation,
        context=InterventionContext(
            run_mode=InterventionRunMode.DISCOVERY,
            application_family="northstar",
            tenant="harbor",
            task_summary="Discovery run requires operator intervention.",
            surface_route="/members/search",
        ),
    )
    return InterventionCoordinator(router, leases), intervention_id


def test_claim_release_and_resume_transitions_share_lease_version() -> None:
    service, intervention_id = coordinator()

    claimed = service.claim(intervention_id, 2, "operator-7")
    assert claimed.intervention.status is InterventionStatus.CLAIMED
    assert claimed.lease.owner.kind is OwnerKind.HUMAN
    assert claimed.lease.version == 3

    released = service.release(intervention_id, 3, "operator-7")
    assert released.intervention.status is InterventionStatus.OPEN
    assert released.lease.owner.kind is OwnerKind.AUTOMATION_PAUSED

    claimed_again = service.claim(intervention_id, 4, "operator-7")
    resuming = service.begin_resume(intervention_id, 5, "operator-7")
    assert claimed_again.lease.version == 5
    assert resuming.intervention.status is InterventionStatus.RESUMING
    assert resuming.lease.owner.kind is OwnerKind.AUTOMATION_PAUSED

    reopened = service.reopen(intervention_id, "Checkpoint no longer matches.")
    assert reopened.intervention.status is InterventionStatus.OPEN
    assert reopened.intervention.operator_id is None
    assert reopened.lease == resuming.lease


def test_transition_rejects_disagreement_between_intervention_and_lease() -> None:
    service, intervention_id = coordinator()
    opened = service.get(intervention_id)

    with pytest.raises(ValueError, match="owner disagree"):
        InterventionTransition(opened.intervention.claim("operator-7"), opened.lease)

    claimed = service.claim(intervention_id, opened.lease.version, "operator-7")
    with pytest.raises(ValueError, match="different operators"):
        InterventionTransition(claimed.intervention.reassign("operator-8"), claimed.lease)


def test_completed_resume_returns_control_to_automation() -> None:
    service, intervention_id = coordinator()
    claimed = service.claim(intervention_id, 2, "operator-7")
    resuming = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    completed = service.complete_resume(
        intervention_id, resuming.lease.version, "Resume checkpoint verified."
    )

    assert completed.intervention.status is InterventionStatus.RESOLVED
    assert completed.intervention.resolution == "Resume checkpoint verified."
    assert completed.lease.owner == AUTOMATION_OWNER
    assert completed.lease.version == resuming.lease.version + 1


def test_stale_claim_and_wrong_operator_fail_closed() -> None:
    service, intervention_id = coordinator()

    with pytest.raises(LeaseConflictError):
        service.claim(intervention_id, 1, "operator-7")

    claimed = service.claim(intervention_id, 2, "operator-7")
    with pytest.raises(ValueError, match="does not own"):
        service.release(intervention_id, claimed.lease.version, "operator-8")


def test_heartbeat_rotates_current_human_lease() -> None:
    service, intervention_id = coordinator()
    claimed = service.claim(intervention_id, 2, "operator-7")

    heartbeat = service.heartbeat(intervention_id, claimed.lease.version, "operator-7")

    assert heartbeat.intervention == claimed.intervention
    assert heartbeat.lease.version == claimed.lease.version + 1
    assert heartbeat.lease.owner.value == "human:operator-7"
    with pytest.raises(ValueError, match="does not own"):
        service.heartbeat(intervention_id, heartbeat.lease.version, "operator-8")


def test_expired_human_claim_can_be_reassigned_but_active_claim_cannot() -> None:
    service, intervention_id = coordinator()
    claimed = service.claim(intervention_id, 2, "operator-7")

    with pytest.raises(LeaseConflictError, match="still active"):
        service.claim(intervention_id, claimed.lease.version, "operator-8")

    later_leases = ControlLeaseService(
        service.leases.repository,
        FrozenClock(claimed.lease.expires_at + timedelta(seconds=1)),
    )
    later = InterventionCoordinator(service.interventions, later_leases)
    reassigned = later.claim(intervention_id, claimed.lease.version, "operator-8")

    assert reassigned.intervention.operator_id == "operator-8"
    assert reassigned.lease.owner.value == "human:operator-8"
    assert reassigned.lease.version == claimed.lease.version + 1


def test_paused_intervention_remains_claimable_after_passive_lease_expiry() -> None:
    service, intervention_id = coordinator()
    paused = service.get(intervention_id).lease
    later = InterventionCoordinator(
        service.interventions,
        ControlLeaseService(
            service.leases.repository,
            FrozenClock(paused.expires_at + timedelta(seconds=1)),
        ),
    )

    claimed = later.claim(intervention_id, paused.version, "operator-7")

    assert claimed.lease.owner.value == "human:operator-7"


@pytest.mark.parametrize("claimed", [False, True])
def test_termination_revokes_control(claimed: bool) -> None:
    service, intervention_id = coordinator()
    version = 2
    operator_id = None
    if claimed:
        transition = service.claim(intervention_id, version, "operator-7")
        version = transition.lease.version
        operator_id = "operator-7"

    terminated = service.terminate(intervention_id, version, operator_id, "Operator ended the run.")

    assert terminated.intervention.status is InterventionStatus.TERMINATED
    assert terminated.lease.owner.kind is OwnerKind.NONE
    with pytest.raises(LeaseConflictError):
        service.leases.assert_can_act(
            str(terminated.lease.session_id), terminated.lease.version, AUTOMATION_OWNER
        )
