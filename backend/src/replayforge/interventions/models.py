"""Immutable intervention and lease aggregates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from replayforge.shared.ids import EntityId
from replayforge.surfaces.models import HumanInput, HumanKeyInput, HumanPointerInput, Viewport


class OwnerKind(StrEnum):
    AUTOMATION = "automation"
    AUTOMATION_PAUSED = "automation_paused"
    HUMAN = "human"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ControlOwner:
    kind: OwnerKind
    principal_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is OwnerKind.HUMAN and not self.principal_id:
            raise ValueError("human control owner requires a principal ID")
        if self.kind is not OwnerKind.HUMAN and self.principal_id is not None:
            raise ValueError("only a human control owner may include a principal ID")

    @property
    def value(self) -> str:
        if self.kind is OwnerKind.HUMAN:
            return f"human:{self.principal_id}"
        return self.kind.value


AUTOMATION_OWNER = ControlOwner(OwnerKind.AUTOMATION)
PAUSED_OWNER = ControlOwner(OwnerKind.AUTOMATION_PAUSED)
NO_OWNER = ControlOwner(OwnerKind.NONE)


@dataclass(frozen=True, slots=True)
class InterventionFrame:
    """Ordered frame exposed to the operator for stale-input protection."""

    content: bytes
    sequence: int
    viewport: Viewport
    next_client_sequence: int = 1

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("frame sequence must be positive")
        if self.next_client_sequence < 1:
            raise ValueError("next client sequence must be positive")


class HumanInputConflictError(RuntimeError):
    """Input references stale or already-consumed operator state."""


@dataclass(frozen=True, slots=True)
class HumanInputCommand:
    client_sequence: int
    source_frame_sequence: int
    viewport: Viewport
    action: HumanInput

    def __post_init__(self) -> None:
        if self.client_sequence < 1 or self.source_frame_sequence < 1:
            raise ValueError("input and source-frame sequences must be positive")
        if isinstance(self.action, HumanPointerInput) and (
            self.action.x >= self.viewport.width or self.action.y >= self.viewport.height
        ):
            raise ValueError("pointer coordinates must fall inside the source viewport")

    def audit_details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "client_sequence": self.client_sequence,
            "source_frame_sequence": self.source_frame_sequence,
            "viewport_height": self.viewport.height,
            "viewport_width": self.viewport.width,
        }
        if isinstance(self.action, HumanPointerInput):
            return {**details, "input_type": "pointer", "x": self.action.x, "y": self.action.y}
        if isinstance(self.action, HumanKeyInput):
            return {**details, "input_type": "key", "key": self.action.key.value}
        return {**details, "input_type": "text", "character_count": len(self.action.text)}


@dataclass(frozen=True, slots=True)
class HumanInputReceipt:
    client_sequence: int
    source_frame_sequence: int


@dataclass(frozen=True, slots=True)
class ControlLease:
    session_id: EntityId
    owner: ControlOwner
    version: int
    issued_at: datetime
    last_heartbeat: datetime
    expires_at: datetime
    intervention_id: EntityId | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("lease version must be positive")
        if self.expires_at <= self.issued_at:
            raise ValueError("lease expiry must be after issuance")


class InterventionStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESUMING = "resuming"
    RESOLVED = "resolved"
    TERMINATED = "terminated"


class InterventionTransitionError(ValueError):
    """Raised for an illegal intervention lifecycle transition."""


@dataclass(frozen=True, slots=True)
class Intervention:
    id: EntityId
    run_id: EntityId
    session_id: EntityId
    trigger_code: str
    explanation: str
    status: InterventionStatus
    created_at: datetime
    operator_id: str | None = None
    resolution: str | None = None

    def claim(self, operator_id: str) -> Intervention:
        if self.status is not InterventionStatus.OPEN:
            raise InterventionTransitionError("only an open intervention can be claimed")
        if not operator_id:
            raise ValueError("operator ID is required")
        return replace(self, status=InterventionStatus.CLAIMED, operator_id=operator_id)

    def release(self) -> Intervention:
        if self.status is not InterventionStatus.CLAIMED:
            raise InterventionTransitionError("only a claimed intervention can be released")
        return replace(self, status=InterventionStatus.OPEN, operator_id=None)

    def begin_resume(self) -> Intervention:
        if self.status is not InterventionStatus.CLAIMED:
            raise InterventionTransitionError("only a claimed intervention can resume")
        return replace(self, status=InterventionStatus.RESUMING)

    def reopen(self, explanation: str) -> Intervention:
        if self.status is not InterventionStatus.RESUMING:
            raise InterventionTransitionError("only a resuming intervention can reopen")
        return replace(
            self,
            status=InterventionStatus.OPEN,
            operator_id=None,
            explanation=explanation,
        )

    def resolve(self, resolution: str) -> Intervention:
        if self.status is not InterventionStatus.RESUMING:
            raise InterventionTransitionError("only a resuming intervention can resolve")
        return replace(self, status=InterventionStatus.RESOLVED, resolution=resolution)

    def terminate(self, resolution: str) -> Intervention:
        if self.status not in {InterventionStatus.OPEN, InterventionStatus.CLAIMED}:
            raise InterventionTransitionError("only an open or claimed intervention can terminate")
        return replace(self, status=InterventionStatus.TERMINATED, resolution=resolution)
