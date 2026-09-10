"""Injectable UTC time source for deterministic domain behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provides timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock backed by the operating system."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """Test clock that always returns one validated instant."""

    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError("FrozenClock requires a timezone-aware instant")

    def now(self) -> datetime:
        return self.instant.astimezone(UTC)
