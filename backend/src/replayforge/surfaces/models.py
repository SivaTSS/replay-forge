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
class SurfaceFrame:
    """One raster frame paired with the viewport it represents."""

    content: bytes
    viewport: Viewport

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("surface frame content cannot be empty")


@dataclass(frozen=True, slots=True)
class SanitizedSurfaceFrame:
    """A PNG whose sensitive regions were masked before capture."""

    content: bytes
    redaction_directives: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("sanitized surface frame must contain PNG content")
        if not self.redaction_directives:
            raise ValueError("sanitized surface frame must declare its masking policy")


class HumanKey(StrEnum):
    ENTER = "Enter"
    ESCAPE = "Escape"
    TAB = "Tab"
    SHIFT_TAB = "Shift+Tab"
    BACKSPACE = "Backspace"
    DELETE = "Delete"
    ARROW_UP = "ArrowUp"
    ARROW_DOWN = "ArrowDown"
    ARROW_LEFT = "ArrowLeft"
    ARROW_RIGHT = "ArrowRight"


@dataclass(frozen=True, slots=True)
class HumanPointerInput:
    x: int
    y: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("pointer coordinates cannot be negative")


@dataclass(frozen=True, slots=True)
class HumanTextInput:
    text: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.text) <= 1_000:
            raise ValueError("human text input must contain between 1 and 1000 characters")


@dataclass(frozen=True, slots=True)
class HumanKeyInput:
    key: HumanKey


type HumanInput = HumanPointerInput | HumanTextInput | HumanKeyInput


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    id: EntityId
    session_id: EntityId
    captured_at: datetime
    route: str
    viewport: Viewport
    fingerprint: str
    landmarks: tuple[str, ...]
    frame_titles: tuple[str, ...] = ()
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
