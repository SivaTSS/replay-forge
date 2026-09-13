from datetime import UTC, datetime, timedelta

import pytest

from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
    LeaseConflictError,
    LeaseNotFoundError,
)
from replayforge.interventions.models import (
    AUTOMATION_OWNER,
    NO_OWNER,
    PAUSED_OWNER,
    ControlLease,
    ControlOwner,
    OwnerKind,
)
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


@pytest.fixture
def service() -> ControlLeaseService:
    return ControlLeaseService(
        InMemoryControlLeaseRepository(),
        FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC)),
        timedelta(seconds=30),
    )


def test_complete_same_session_handoff_increments_every_lease_version(
    service: ControlLeaseService,
) -> None:
    session_id = new_id(EntityKind.SESSION)
    intervention_id = new_id(EntityKind.INTERVENTION)

    automation = service.create_for_automation(session_id)
    paused = service.pause(session_id, automation.version, intervention_id)
    human = service.claim(session_id, paused.version, intervention_id, operator_id="operator-7")
    resuming = service.begin_resume(session_id, human.version, "operator-7")
    resumed = service.complete_resume(session_id, resuming.version)

    assert {lease.session_id for lease in (automation, paused, human, resuming, resumed)} == {
        session_id
    }
    assert [
        automation.version,
        paused.version,
        human.version,
        resuming.version,
        resumed.version,
    ] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert paused.owner == PAUSED_OWNER
    assert human.owner == ControlOwner(OwnerKind.HUMAN, "operator-7")
    assert resumed.owner == AUTOMATION_OWNER
    assert resumed.intervention_id is None


def test_two_operators_cannot_claim_same_version(service: ControlLeaseService) -> None:
    session_id = new_id(EntityKind.SESSION)
    intervention_id = new_id(EntityKind.INTERVENTION)
    initial = service.create_for_automation(session_id)
    paused = service.pause(session_id, initial.version, intervention_id)

    winner = service.claim(session_id, paused.version, intervention_id, "operator-1")

    with pytest.raises(LeaseConflictError, match="stale"):
        service.claim(session_id, paused.version, intervention_id, "operator-2")
    assert winner.owner.value == "human:operator-1"


def test_claim_cannot_substitute_another_intervention(
    service: ControlLeaseService,
) -> None:
    session_id = new_id(EntityKind.SESSION)
    initial = service.create_for_automation(session_id)
    paused = service.pause(session_id, initial.version, new_id(EntityKind.INTERVENTION))

    with pytest.raises(LeaseConflictError, match="not bound"):
        service.claim(
            session_id,
            paused.version,
            new_id(EntityKind.INTERVENTION),
            "operator-1",
        )


def test_automation_cannot_act_after_pause(service: ControlLeaseService) -> None:
    session_id = new_id(EntityKind.SESSION)
    initial = service.create_for_automation(session_id)
    service.pause(session_id, initial.version, new_id(EntityKind.INTERVENTION))

    with pytest.raises(LeaseConflictError, match="stale"):
        service.assert_can_act(session_id, initial.version, AUTOMATION_OWNER)


def test_heartbeat_requires_current_owner_and_increments_version(
    service: ControlLeaseService,
) -> None:
    session_id = new_id(EntityKind.SESSION)
    initial = service.create_for_automation(session_id)

    heartbeat = service.heartbeat(session_id, initial.version, AUTOMATION_OWNER)

    assert heartbeat.version == initial.version + 1
    assert heartbeat.owner == AUTOMATION_OWNER


def test_terminate_removes_all_control(service: ControlLeaseService) -> None:
    session_id = new_id(EntityKind.SESSION)
    initial = service.create_for_automation(session_id)

    terminated = service.terminate(session_id, initial.version, expected_owner=AUTOMATION_OWNER)

    assert terminated.owner == NO_OWNER


def test_invalid_owner_shapes_are_rejected() -> None:
    with pytest.raises(ValueError, match="requires a principal"):
        ControlOwner(OwnerKind.HUMAN)
    with pytest.raises(ValueError, match="only a human"):
        ControlOwner(OwnerKind.AUTOMATION, "operator-1")


def test_invalid_lease_is_rejected() -> None:
    now = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="version"):
        ControlLease(
            new_id(EntityKind.SESSION), AUTOMATION_OWNER, 0, now, now, now + timedelta(seconds=1)
        )
    with pytest.raises(ValueError, match="ordered"):
        ControlLease(new_id(EntityKind.SESSION), AUTOMATION_OWNER, 1, now, now, now)
    with pytest.raises(ValueError, match="intervention binding"):
        ControlLease(
            new_id(EntityKind.SESSION),
            PAUSED_OWNER,
            1,
            now,
            now,
            now + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="intervention binding"):
        ControlLease(
            new_id(EntityKind.SESSION),
            AUTOMATION_OWNER,
            1,
            now,
            now,
            now + timedelta(seconds=1),
            new_id(EntityKind.INTERVENTION),
        )
    with pytest.raises(ValueError, match="offset"):
        naive = datetime(2026, 9, 10, 12, 30)
        ControlLease(
            new_id(EntityKind.SESSION),
            AUTOMATION_OWNER,
            1,
            naive,
            naive,
            naive + timedelta(seconds=1),
        )


def test_repository_rejects_duplicate_and_invalid_replacement() -> None:
    repository = InMemoryControlLeaseRepository()
    now = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
    session_id = new_id(EntityKind.SESSION)
    lease = ControlLease(session_id, AUTOMATION_OWNER, 1, now, now, now + timedelta(seconds=30))
    repository.create(lease)
    with pytest.raises(LeaseConflictError, match="already exists"):
        repository.create(lease)
    with pytest.raises(ValueError, match="session identity"):
        repository.compare_and_swap(
            session_id,
            1,
            AUTOMATION_OWNER,
            ControlLease(
                new_id(EntityKind.SESSION),
                PAUSED_OWNER,
                2,
                now,
                now,
                now + timedelta(seconds=30),
                new_id(EntityKind.INTERVENTION),
            ),
        )
    with pytest.raises(ValueError, match="increment exactly once"):
        repository.compare_and_swap(session_id, 1, AUTOMATION_OWNER, lease)


def test_missing_lease_is_explicit(service: ControlLeaseService) -> None:
    with pytest.raises(LeaseNotFoundError):
        service.repository.get(new_id(EntityKind.SESSION))


def test_service_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError, match="TTL"):
        ControlLeaseService(
            InMemoryControlLeaseRepository(),
            FrozenClock(datetime(2026, 9, 10, tzinfo=UTC)),
            timedelta(0),
        )


def test_claim_requires_operator(service: ControlLeaseService) -> None:
    session_id = new_id(EntityKind.SESSION)
    initial = service.create_for_automation(session_id)
    intervention_id = new_id(EntityKind.INTERVENTION)
    paused = service.pause(session_id, initial.version, intervention_id)

    with pytest.raises(ValueError, match="operator ID"):
        service.claim(session_id, paused.version, intervention_id, "")
