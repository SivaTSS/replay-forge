from datetime import UTC, datetime, timedelta, timezone

import pytest

from replayforge.shared.clock import Clock, FrozenClock, SystemClock


def read_clock(clock: Clock) -> datetime:
    return clock.now()


def test_system_clock_returns_aware_utc_instant() -> None:
    instant = read_clock(SystemClock())

    assert instant.tzinfo is UTC
    assert instant.utcoffset() == timedelta(0)


def test_frozen_clock_normalizes_offset_to_utc() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 8, 30, tzinfo=timezone(timedelta(hours=-4))))

    assert read_clock(clock) == datetime(2026, 9, 10, 12, 30, tzinfo=UTC)


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 9, 10, 12, 30))
