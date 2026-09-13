from datetime import UTC, datetime, timedelta

import pytest

from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import (
    ActionReceipt,
    ActionStatus,
    NormalizedObservation,
    ResolvedTarget,
    SanitizedSurfaceFrame,
    Viewport,
)


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
    with pytest.raises(ValueError, match="only failed"):
        ActionReceipt(ActionStatus.COMPLETED, now, now, error_code="unexpected")
    with pytest.raises(ValueError, match="offset"):
        ActionReceipt(ActionStatus.COMPLETED, now.replace(tzinfo=None), now)


def test_observation_requires_typed_identity_and_canonical_location() -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    values = {
        "id": new_id(EntityKind.EVENT),
        "session_id": new_id(EntityKind.SESSION),
        "captured_at": now,
        "route": "/members/search",
        "viewport": Viewport(1280, 800),
        "fingerprint": "frame-state",
        "landmarks": ("Member Search",),
    }

    with pytest.raises(ValueError, match="evt identifier"):
        NormalizedObservation(**(values | {"id": new_id(EntityKind.RUN)}))
    with pytest.raises(ValueError, match="absolute path"):
        NormalizedObservation(**(values | {"route": "/members/search?member=12345"}))
    with pytest.raises(ValueError, match="unique"):
        NormalizedObservation(**(values | {"landmarks": ("Member Search", "Member Search")}))


def test_ids_remain_opaque_across_surface_contract() -> None:
    observation_id = new_id(EntityKind.EVENT)

    assert str(observation_id).startswith("evt_")


def test_sanitized_surface_frame_requires_png_and_masking_policy() -> None:
    with pytest.raises(ValueError, match="PNG"):
        SanitizedSurfaceFrame(b"not-an-image", ("mask:inputs",))
    with pytest.raises(ValueError, match="masking policy"):
        SanitizedSurfaceFrame(b"\x89PNG\r\n\x1a\nframe", ())
