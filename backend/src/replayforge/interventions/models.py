"""Immutable intervention and lease aggregates."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from replayforge.shared.ids import EntityId, EntityKind, parse_id
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
        if self.kind is OwnerKind.HUMAN and (
            self.principal_id is None or not self.principal_id.strip()
        ):
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
        parse_id(str(self.session_id), EntityKind.SESSION)
        if self.intervention_id is not None:
            parse_id(str(self.intervention_id), EntityKind.INTERVENTION)
        if self.version < 1:
            raise ValueError("lease version must be positive")
        timestamps = (self.issued_at, self.last_heartbeat, self.expires_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("lease timestamps must include an offset")
        if not self.issued_at <= self.last_heartbeat < self.expires_at:
            raise ValueError("lease timestamps must be ordered from issuance through expiry")
        requires_intervention = self.owner.kind in {
            OwnerKind.AUTOMATION_PAUSED,
            OwnerKind.HUMAN,
        }
        if requires_intervention != (self.intervention_id is not None):
            raise ValueError("paused and human leases require exactly one intervention binding")


class InterventionStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESUMING = "resuming"
    RESOLVED = "resolved"
    TERMINATED = "terminated"


class InterventionRunMode(StrEnum):
    DISCOVERY = "discovery"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class InterventionContext:
    """Safe operator-routing context; invocation values never belong here."""

    run_mode: InterventionRunMode
    application_family: str
    tenant: str
    task_summary: str
    surface_route: str
    step_id: str | None = None
    capability_id: str | None = None
    capability_version: str | None = None
    capability_name: str | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.application_family,
                self.tenant,
                self.task_summary,
                self.surface_route,
            )
        ):
            raise ValueError("intervention context requires non-empty routing fields")
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", self.application_family) is None:
            raise ValueError("intervention application family is invalid")
        if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.tenant) is None:
            raise ValueError("intervention tenant is invalid")
        if len(self.task_summary) > 1_000:
            raise ValueError("intervention task summary is too long")
        if (
            not self.surface_route.startswith("/")
            or "?" in self.surface_route
            or "#" in self.surface_route
            or "//" in self.surface_route
            or ".." in self.surface_route
        ):
            raise ValueError("intervention surface route must be a safe absolute path")
        if self.step_id is not None and re.fullmatch(r"[a-z][a-z0-9_.-]+", self.step_id) is None:
            raise ValueError("intervention step ID is invalid")
        capability_fields = (
            self.capability_id,
            self.capability_version,
            self.capability_name,
        )
        if self.run_mode is InterventionRunMode.REPLAY and not all(capability_fields):
            raise ValueError("replay intervention context requires capability metadata")
        if self.run_mode is InterventionRunMode.DISCOVERY and any(capability_fields):
            raise ValueError("discovery intervention context cannot identify a capability")
        if (
            self.capability_id is not None
            and re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", self.capability_id) is None
        ):
            raise ValueError("intervention capability ID is invalid")
        if (
            self.capability_version is not None
            and re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
                self.capability_version,
            )
            is None
        ):
            raise ValueError("intervention capability version is invalid")


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
    context: InterventionContext
    operator_id: str | None = None
    resolution: str | None = None

    def __post_init__(self) -> None:
        parse_id(str(self.id), EntityKind.INTERVENTION)
        parse_id(str(self.run_id), EntityKind.RUN)
        parse_id(str(self.session_id), EntityKind.SESSION)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.trigger_code) is None:
            raise ValueError("intervention trigger code is invalid")
        if not self.explanation.strip() or len(self.explanation) > 1_000:
            raise ValueError("intervention explanation must be non-empty and bounded")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("intervention timestamp must include an offset")
        requires_operator = self.status in {
            InterventionStatus.CLAIMED,
            InterventionStatus.RESUMING,
            InterventionStatus.RESOLVED,
        }
        if requires_operator and self.operator_id is None:
            raise ValueError("intervention status and operator ownership disagree")
        if self.status is InterventionStatus.OPEN and self.operator_id is not None:
            raise ValueError("intervention status and operator ownership disagree")
        requires_resolution = self.status in {
            InterventionStatus.RESOLVED,
            InterventionStatus.TERMINATED,
        }
        if requires_resolution != (self.resolution is not None):
            raise ValueError("intervention terminal status and resolution disagree")
        if self.operator_id is not None and not self.operator_id.strip():
            raise ValueError("intervention operator ID cannot be empty")
        if self.resolution is not None and not self.resolution.strip():
            raise ValueError("intervention resolution cannot be empty")

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

    def reassign(self, operator_id: str) -> Intervention:
        if self.status is not InterventionStatus.CLAIMED:
            raise InterventionTransitionError("only a claimed intervention can be reassigned")
        if not operator_id:
            raise ValueError("operator ID is required")
        return replace(self, operator_id=operator_id)

    def begin_resume(self) -> Intervention:
        if self.status is not InterventionStatus.CLAIMED:
            raise InterventionTransitionError("only a claimed intervention can resume")
        return replace(self, status=InterventionStatus.RESUMING)

    def reopen(self, explanation: str) -> Intervention:
        if self.status is not InterventionStatus.RESUMING:
            raise InterventionTransitionError("only a resuming intervention can reopen")
        if not explanation.strip():
            raise ValueError("reopen explanation is required")
        return replace(
            self,
            status=InterventionStatus.OPEN,
            operator_id=None,
            explanation=explanation,
        )

    def resolve(self, resolution: str) -> Intervention:
        if self.status is not InterventionStatus.RESUMING:
            raise InterventionTransitionError("only a resuming intervention can resolve")
        if not resolution.strip():
            raise ValueError("resolution is required")
        return replace(self, status=InterventionStatus.RESOLVED, resolution=resolution)

    def terminate(self, resolution: str) -> Intervention:
        if self.status not in {InterventionStatus.OPEN, InterventionStatus.CLAIMED}:
            raise InterventionTransitionError("only an open or claimed intervention can terminate")
        if not resolution.strip():
            raise ValueError("resolution is required")
        return replace(self, status=InterventionStatus.TERMINATED, resolution=resolution)
