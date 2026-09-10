"""Immutable intervention and lease aggregates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from replayforge.shared.ids import EntityId


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
