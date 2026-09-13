"""Versioned control-lease transitions with an atomic in-memory adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from threading import Lock

from replayforge.interventions.models import (
    AUTOMATION_OWNER,
    NO_OWNER,
    PAUSED_OWNER,
    ControlLease,
    ControlOwner,
    OwnerKind,
)
from replayforge.interventions.ports import ControlLeaseRepository
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityId, EntityKind, parse_id


class LeaseConflictError(RuntimeError):
    """The caller acted on stale ownership or version state."""


class LeaseExpiredError(LeaseConflictError):
    """The active owner stopped renewing its control lease."""


class LeaseNotFoundError(KeyError):
    """No control lease exists for the requested session."""


@dataclass(slots=True)
class InMemoryControlLeaseRepository:
    """Thread-safe contract adapter; production uses database compare-and-swap."""

    _leases: dict[str, ControlLease] = field(init=False, default_factory=dict)
    _lock: Lock = field(init=False, default_factory=Lock)

    def create(self, lease: ControlLease) -> ControlLease:
        with self._lock:
            key = str(lease.session_id)
            if key in self._leases:
                raise LeaseConflictError("a lease already exists for this session")
            self._leases[key] = lease
            return lease

    def get(self, session_id: str) -> ControlLease:
        with self._lock:
            try:
                return self._leases[session_id]
            except KeyError as exc:
                raise LeaseNotFoundError(session_id) from exc

    def compare_and_swap(
        self,
        session_id: str,
        expected_version: int,
        expected_owner: ControlOwner,
        replacement: ControlLease,
    ) -> ControlLease:
        with self._lock:
            current = self._leases.get(session_id)
            if current is None:
                raise LeaseNotFoundError(session_id)
            if current.version != expected_version or current.owner != expected_owner:
                raise LeaseConflictError("control lease version or owner is stale")
            if replacement.session_id != current.session_id:
                raise ValueError("replacement lease cannot change session identity")
            if replacement.version != current.version + 1:
                raise ValueError("replacement lease version must increment exactly once")
            self._leases[session_id] = replacement
            return replacement


@dataclass(frozen=True, slots=True)
class ControlLeaseService:
    repository: ControlLeaseRepository
    clock: Clock
    ttl: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            raise ValueError("control lease TTL must be positive")

    def create_for_automation(self, session_id: str) -> ControlLease:
        parsed_session_id = parse_id(session_id, EntityKind.SESSION)
        now = self.clock.now()
        return self.repository.create(
            ControlLease(
                session_id=parsed_session_id,
                owner=AUTOMATION_OWNER,
                version=1,
                issued_at=now,
                last_heartbeat=now,
                expires_at=now + self.ttl,
            )
        )

    def pause(self, session_id: str, expected_version: int, intervention_id: str) -> ControlLease:
        return self._transfer(
            session_id,
            expected_version,
            AUTOMATION_OWNER,
            PAUSED_OWNER,
            parse_id(intervention_id, EntityKind.INTERVENTION),
        )

    def claim(
        self,
        session_id: str,
        expected_version: int,
        intervention_id: str,
        operator_id: str,
    ) -> ControlLease:
        if not operator_id:
            raise ValueError("operator ID is required")
        parsed_intervention_id = parse_id(intervention_id, EntityKind.INTERVENTION)
        current = self.repository.get(session_id)
        if current.intervention_id != parsed_intervention_id:
            raise LeaseConflictError("intervention is not bound to this control lease")
        return self._transfer(
            session_id,
            expected_version,
            PAUSED_OWNER,
            ControlOwner(OwnerKind.HUMAN, operator_id),
            parsed_intervention_id,
            require_unexpired=False,
        )

    def reclaim_expired(
        self,
        session_id: str,
        expected_version: int,
        intervention_id: str,
        operator_id: str,
    ) -> ControlLease:
        current = self.repository.get(session_id)
        parsed_intervention_id = parse_id(intervention_id, EntityKind.INTERVENTION)
        if current.version != expected_version or current.intervention_id != parsed_intervention_id:
            raise LeaseConflictError("control lease version or intervention is stale")
        if current.owner.kind is not OwnerKind.HUMAN:
            raise LeaseConflictError("only an expired human lease can be reclaimed")
        if current.expires_at > self.clock.now():
            raise LeaseConflictError("the current human control lease is still active")
        return self._transfer(
            session_id,
            expected_version,
            current.owner,
            ControlOwner(OwnerKind.HUMAN, operator_id),
            parsed_intervention_id,
            require_unexpired=False,
        )

    def release(self, session_id: str, expected_version: int, operator_id: str) -> ControlLease:
        current = self.repository.get(session_id)
        return self._transfer(
            session_id,
            expected_version,
            ControlOwner(OwnerKind.HUMAN, operator_id),
            PAUSED_OWNER,
            current.intervention_id,
        )

    def begin_resume(
        self, session_id: str, expected_version: int, operator_id: str
    ) -> ControlLease:
        return self.release(session_id, expected_version, operator_id)

    def complete_resume(self, session_id: str, expected_version: int) -> ControlLease:
        return self._transfer(
            session_id,
            expected_version,
            PAUSED_OWNER,
            AUTOMATION_OWNER,
            None,
        )

    def terminate(
        self, session_id: str, expected_version: int, expected_owner: ControlOwner
    ) -> ControlLease:
        return self._transfer(
            session_id,
            expected_version,
            expected_owner,
            NO_OWNER,
            None,
            require_unexpired=expected_owner != PAUSED_OWNER,
        )

    def assert_can_act(
        self, session_id: str, expected_version: int, owner: ControlOwner
    ) -> ControlLease:
        current = self.repository.get(session_id)
        if current.version != expected_version or current.owner != owner:
            raise LeaseConflictError("control lease version or owner is stale")
        if current.expires_at <= self.clock.now():
            raise LeaseExpiredError("control lease has expired")
        return current

    def heartbeat(
        self, session_id: str, expected_version: int, owner: ControlOwner
    ) -> ControlLease:
        current = self.assert_can_act(session_id, expected_version, owner)
        replacement = self.prepare_heartbeat(current, expected_version, owner)
        return self.repository.compare_and_swap(session_id, expected_version, owner, replacement)

    def prepare_heartbeat(
        self, current: ControlLease, expected_version: int, owner: ControlOwner
    ) -> ControlLease:
        """Build a validated heartbeat without persisting it for a larger transaction."""
        self._assert_current(current, expected_version, owner)
        now = self.clock.now()
        return replace(
            current,
            version=current.version + 1,
            last_heartbeat=now,
            expires_at=now + self.ttl,
        )

    def prepare_transfer(
        self,
        current: ControlLease,
        expected_version: int,
        expected_owner: ControlOwner,
        next_owner: ControlOwner,
        intervention_id: EntityId | None,
        *,
        require_unexpired: bool = True,
    ) -> ControlLease:
        """Build a validated ownership transfer without writing either aggregate."""
        self._assert_current(current, expected_version, expected_owner, require_unexpired)
        now = self.clock.now()
        return ControlLease(
            session_id=current.session_id,
            owner=next_owner,
            version=current.version + 1,
            issued_at=now,
            last_heartbeat=now,
            expires_at=now + self.ttl,
            intervention_id=intervention_id,
        )

    def _transfer(
        self,
        session_id: str,
        expected_version: int,
        expected_owner: ControlOwner,
        next_owner: ControlOwner,
        intervention_id: EntityId | None,
        *,
        require_unexpired: bool = True,
    ) -> ControlLease:
        current = self.repository.get(session_id)
        replacement = self.prepare_transfer(
            current,
            expected_version,
            expected_owner,
            next_owner,
            intervention_id,
            require_unexpired=require_unexpired,
        )
        return self.repository.compare_and_swap(
            session_id, expected_version, expected_owner, replacement
        )

    def _assert_current(
        self,
        current: ControlLease,
        expected_version: int,
        expected_owner: ControlOwner,
        require_unexpired: bool = True,
    ) -> None:
        if current.version != expected_version or current.owner != expected_owner:
            raise LeaseConflictError("control lease version or owner is stale")
        if require_unexpired and current.expires_at <= self.clock.now():
            raise LeaseExpiredError("control lease has expired")
