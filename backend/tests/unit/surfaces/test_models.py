from datetime import UTC, datetime, timedelta

import pytest

from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import ActionReceipt, ActionStatus, ResolvedTarget, Viewport


def test_surface_value_objects_reject_impossible_state() -> None:
    with pytest.raises(ValueError, match="viewport"):
        Viewport(0, 800)
    with pytest.raises(ValueError, match="exactly one"):
        ResolvedTarget("handle", "ambiguous", 0, 2)


def test_failed_receipt_requires_error_and_valid_time_order() -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    with pytest.raises(ValueError, match="error code"):
        ActionReceipt(ActionStatus.FAILED, now, now)
    with pytest.raises(ValueError, match="cannot precede"):
        ActionReceipt(ActionStatus.COMPLETED, now, now - timedelta(seconds=1))


def test_ids_remain_opaque_across_surface_contract() -> None:
    observation_id = new_id(EntityKind.EVENT)

    assert str(observation_id).startswith("evt_")
