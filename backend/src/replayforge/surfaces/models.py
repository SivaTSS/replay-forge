"""Serializable surface facts without browser or desktop framework handles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from replayforge.policy.types import Risk
from replayforge.shared.ids import EntityId

SurfaceSessionId = NewType("SurfaceSessionId", str)


class SurfaceError(RuntimeError):
    """A classified surface failure safe to expose in a run result."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool = False,
        effect_absent: bool = False,
        intervention_recommended: bool = False,
        expected: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.recoverable = recoverable
        self.effect_absent = effect_absent
        self.intervention_recommended = intervention_recommended
        self.expected = expected
        self.observed = observed


class ActionStatus(StrEnum):
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Viewport:
    width: int
    height: int
    device_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.device_scale <= 0:
            raise ValueError("viewport dimensions and scale must be positive")


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    id: EntityId
    session_id: EntityId
    captured_at: datetime
    route: str
    viewport: Viewport
    fingerprint: str
    landmarks: tuple[str, ...]
    active_element: str | None = None
    dialog_text: str | None = None
    screenshot_evidence_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    handle: str
    description: str
    candidate_index: int
    observed_count: int
    registered_risk: Risk | None = None

    def __post_init__(self) -> None:
        if self.candidate_index < 0 or self.observed_count != 1:
            raise ValueError("a resolved target must identify exactly one candidate match")


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    status: ActionStatus
    started_at: datetime
    completed_at: datetime
    observed_effect: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.completed_at < self.started_at:
            raise ValueError("action completion cannot precede its start")
        if self.status is ActionStatus.FAILED and not self.error_code:
            raise ValueError("failed action receipt requires an error code")
