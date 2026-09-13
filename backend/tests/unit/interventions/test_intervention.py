from datetime import UTC, datetime

import pytest

from replayforge.interventions.models import (
    Intervention,
    InterventionContext,
    InterventionRunMode,
    InterventionStatus,
    InterventionTransitionError,
)
from replayforge.shared.ids import EntityKind, new_id


@pytest.fixture
def intervention() -> Intervention:
    return Intervention(
        id=new_id(EntityKind.INTERVENTION),
        run_id=new_id(EntityKind.RUN),
        session_id=new_id(EntityKind.SESSION),
        trigger_code="unexpected_dialog",
        explanation="An unclassified confirmation dialog is visible.",
        status=InterventionStatus.OPEN,
        created_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
        context=InterventionContext(
            run_mode=InterventionRunMode.DISCOVERY,
            application_family="northstar",
            tenant="harbor",
            task_summary="Discovery run requires operator intervention.",
            surface_route="/members/search",
        ),
    )


def test_valid_resume_lifecycle(intervention: Intervention) -> None:
    claimed = intervention.claim("operator-7")
    resuming = claimed.begin_resume()
    resolved = resuming.resolve("Dialog dismissed and checkpoint verified.")

    assert claimed.status is InterventionStatus.CLAIMED
    assert resuming.status is InterventionStatus.RESUMING
    assert resolved.status is InterventionStatus.RESOLVED
    assert resolved.operator_id == "operator-7"


def test_release_returns_intervention_to_queue(intervention: Intervention) -> None:
    released = intervention.claim("operator-7").release()

    assert released.status is InterventionStatus.OPEN
    assert released.operator_id is None


def test_failed_resume_reopens_with_new_reason(intervention: Intervention) -> None:
    reopened = (
        intervention.claim("operator-7").begin_resume().reopen("Member identity no longer matches.")
    )

    assert reopened.status is InterventionStatus.OPEN
    assert reopened.explanation == "Member identity no longer matches."


@pytest.mark.parametrize("claimed", [False, True])
def test_open_or_claimed_intervention_can_terminate(
    intervention: Intervention, claimed: bool
) -> None:
    current = intervention.claim("operator-7") if claimed else intervention

    terminated = current.terminate("Operator ended the run.")

    assert terminated.status is InterventionStatus.TERMINATED


def test_illegal_transitions_fail_closed(intervention: Intervention) -> None:
    with pytest.raises(InterventionTransitionError):
        intervention.release()
    with pytest.raises(InterventionTransitionError):
        intervention.begin_resume()
    with pytest.raises(InterventionTransitionError):
        intervention.reopen("invalid")
    with pytest.raises(InterventionTransitionError):
        intervention.resolve("invalid")
    with pytest.raises(InterventionTransitionError):
        intervention.claim("operator-1").begin_resume().terminate("invalid")
    with pytest.raises(InterventionTransitionError):
        intervention.reassign("operator-1")
    with pytest.raises(ValueError, match="operator ID"):
        intervention.claim("operator-1").reassign("")


def test_claim_requires_operator_identity(intervention: Intervention) -> None:
    with pytest.raises(ValueError, match="operator ID"):
        intervention.claim("")


@pytest.mark.parametrize(
    ("status", "operator_id", "resolution", "message"),
    [
        (InterventionStatus.OPEN, "operator-7", None, "operator ownership"),
        (InterventionStatus.CLAIMED, None, None, "operator ownership"),
        (InterventionStatus.RESOLVED, "operator-7", None, "resolution"),
        (InterventionStatus.TERMINATED, None, None, "resolution"),
    ],
)
def test_intervention_rejects_impossible_lifecycle_state(
    intervention: Intervention,
    status: InterventionStatus,
    operator_id: str | None,
    resolution: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        Intervention(
            id=intervention.id,
            run_id=intervention.run_id,
            session_id=intervention.session_id,
            trigger_code=intervention.trigger_code,
            explanation=intervention.explanation,
            status=status,
            created_at=intervention.created_at,
            context=intervention.context,
            operator_id=operator_id,
            resolution=resolution,
        )


def test_intervention_context_separates_replay_and_discovery_metadata() -> None:
    replay = InterventionContext(
        run_mode=InterventionRunMode.REPLAY,
        application_family="northstar",
        tenant="harbor",
        task_summary="Look up savings balance.",
        surface_route="/members/search",
        capability_id="member.lookup_savings_balance",
        capability_version="2.0.0",
        capability_name="Lookup savings balance",
    )
    assert replay.capability_version == "2.0.0"

    with pytest.raises(ValueError, match="capability metadata"):
        InterventionContext(
            run_mode=InterventionRunMode.REPLAY,
            application_family="northstar",
            tenant="harbor",
            task_summary="Task",
            surface_route="/",
        )
    with pytest.raises(ValueError, match="cannot identify"):
        InterventionContext(
            run_mode=InterventionRunMode.DISCOVERY,
            application_family="northstar",
            tenant="harbor",
            task_summary="Discover task",
            surface_route="/",
            capability_id="member.lookup",
        )
    with pytest.raises(ValueError, match="non-empty"):
        InterventionContext(
            run_mode=InterventionRunMode.DISCOVERY,
            application_family="",
            tenant="harbor",
            task_summary="Discover task",
            surface_route="/",
        )
