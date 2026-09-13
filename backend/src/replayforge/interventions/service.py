"""Lease-coordinated intervention lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
    LeaseConflictError,
)
from replayforge.interventions.models import (
    AUTOMATION_OWNER,
    NO_OWNER,
    PAUSED_OWNER,
    ControlLease,
    ControlOwner,
    Intervention,
    InterventionRunMode,
    InterventionStatus,
    InterventionTransitionError,
    OwnerKind,
)
from replayforge.interventions.ports import InterventionTransitionRepository
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.transactions import (
    InMemoryInterventionTransitionRepository,
)
from replayforge.interventions.transactions import (
    InterventionTransition as InterventionTransition,
)
from replayforge.runs.results import RunResult


class InterventionAuthorizationError(ValueError):
    """The operator is not authorized for the requested intervention transition."""


@dataclass(frozen=True, slots=True)
class InterventionResume:
    transition: InterventionTransition
    result: RunResult | None = None


@dataclass(frozen=True, slots=True)
class InterventionCoordinator:
    interventions: InMemoryInterventionRouter
    leases: ControlLeaseService
    transition_repository: InterventionTransitionRepository | None = None

    def __post_init__(self) -> None:
        if self.transition_repository is not None:
            return
        if not isinstance(self.leases.repository, InMemoryControlLeaseRepository):
            raise TypeError("a durable coordinator requires a transition repository")
        object.__setattr__(
            self,
            "transition_repository",
            InMemoryInterventionTransitionRepository(
                self.interventions,
                self.leases.repository,
            ),
        )

    @property
    def state(self) -> InterventionTransitionRepository:
        assert self.transition_repository is not None
        return self.transition_repository

    def get(self, intervention_id: str) -> InterventionTransition:
        return InterventionTransition(*self.state.get(intervention_id))

    def list_active(
        self, run_mode: InterventionRunMode | None = None
    ) -> tuple[InterventionTransition, ...]:
        return tuple(
            InterventionTransition(intervention, lease)
            for intervention, lease in self.state.list_active(run_mode)
        )

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        if current.status is InterventionStatus.OPEN:
            replacement = current.claim(operator_id)
            if current_lease.intervention_id != current.id:
                raise LeaseConflictError("intervention is not bound to this control lease")
            lease = self.leases.prepare_transfer(
                current_lease,
                expected_lease_version,
                PAUSED_OWNER,
                ControlOwner(OwnerKind.HUMAN, operator_id),
                current.id,
                require_unexpired=False,
            )
        elif current.status is InterventionStatus.CLAIMED:
            replacement = current.reassign(operator_id)
            if (
                current_lease.intervention_id != current.id
                or current_lease.owner.kind is not OwnerKind.HUMAN
            ):
                raise LeaseConflictError("only a bound, expired human lease can be reclaimed")
            if current_lease.expires_at > self.leases.clock.now():
                raise LeaseConflictError("the current human control lease is still active")
            lease = self.leases.prepare_transfer(
                current_lease,
                expected_lease_version,
                current_lease.owner,
                ControlOwner(OwnerKind.HUMAN, operator_id),
                current.id,
                require_unexpired=False,
            )
        else:
            raise InterventionTransitionError("only an open or expired claim can be claimed")
        return self._commit(current, current_lease, replacement, lease)

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        if current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        replacement = current.release()
        lease = self.leases.prepare_transfer(
            current_lease,
            expected_lease_version,
            ControlOwner(OwnerKind.HUMAN, operator_id),
            PAUSED_OWNER,
            current.id,
        )
        return self._commit(current, current_lease, replacement, lease)

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        if current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        replacement = current.begin_resume()
        lease = self.leases.prepare_transfer(
            current_lease,
            expected_lease_version,
            ControlOwner(OwnerKind.HUMAN, operator_id),
            PAUSED_OWNER,
            current.id,
        )
        return self._commit(current, current_lease, replacement, lease)

    def reopen(self, intervention_id: str, explanation: str) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        replacement = current.reopen(explanation)
        if current_lease.owner != PAUSED_OWNER:
            raise RuntimeError("a reopened intervention must retain paused ownership")
        return self._commit(current, current_lease, replacement, current_lease)

    def complete_resume(
        self, intervention_id: str, expected_lease_version: int, resolution: str
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        replacement = current.resolve(resolution)
        lease = self.leases.prepare_transfer(
            current_lease,
            expected_lease_version,
            PAUSED_OWNER,
            AUTOMATION_OWNER,
            None,
        )
        return self._commit(current, current_lease, replacement, lease)

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        if current.status is not InterventionStatus.CLAIMED or current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        owner = ControlOwner(OwnerKind.HUMAN, operator_id)
        lease = self.leases.prepare_heartbeat(current_lease, expected_lease_version, owner)
        return self._commit(current, current_lease, current, lease)

    def _commit(
        self,
        current: Intervention,
        current_lease: ControlLease,
        replacement: Intervention,
        replacement_lease: ControlLease,
    ) -> InterventionTransition:
        return InterventionTransition(
            *self.state.compare_and_swap(
                str(current.id),
                current.status,
                current_lease.version,
                current_lease.owner,
                replacement,
                replacement_lease,
            )
        )

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition:
        current, current_lease = self.state.get(intervention_id)
        expected_owner: ControlOwner
        if current.status is InterventionStatus.OPEN:
            expected_owner = PAUSED_OWNER
        elif current.status is InterventionStatus.CLAIMED and current.operator_id == operator_id:
            expected_owner = ControlOwner(OwnerKind.HUMAN, operator_id)
        else:
            raise InterventionAuthorizationError(
                "operator cannot terminate this intervention state"
            )
        replacement = current.terminate(resolution)
        lease = self.leases.prepare_transfer(
            current_lease,
            expected_lease_version,
            expected_owner,
            NO_OWNER,
            None,
            require_unexpired=expected_owner != PAUSED_OWNER,
        )
        return self._commit(current, current_lease, replacement, lease)
