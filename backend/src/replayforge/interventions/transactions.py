"""Atomic in-memory intervention and control-lease state transitions."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.interventions.leases import (
    InMemoryControlLeaseRepository,
    LeaseConflictError,
    LeaseNotFoundError,
)
from replayforge.interventions.models import (
    ControlLease,
    ControlOwner,
    Intervention,
    InterventionRunMode,
    InterventionStatus,
    OwnerKind,
)
from replayforge.interventions.router import (
    InMemoryInterventionRouter,
    InterventionConflictError,
    InterventionNotFoundError,
)
from replayforge.surfaces.models import NormalizedObservation


@dataclass(frozen=True, slots=True)
class InterventionTransition:
    intervention: Intervention
    lease: ControlLease

    def __post_init__(self) -> None:
        intervention = self.intervention
        lease = self.lease
        if intervention.session_id != lease.session_id:
            raise ValueError("intervention and lease must identify the same session")
        expected_owner = {
            InterventionStatus.OPEN: OwnerKind.AUTOMATION_PAUSED,
            InterventionStatus.CLAIMED: OwnerKind.HUMAN,
            InterventionStatus.RESUMING: OwnerKind.AUTOMATION_PAUSED,
            InterventionStatus.RESOLVED: OwnerKind.AUTOMATION,
            InterventionStatus.TERMINATED: OwnerKind.NONE,
        }[intervention.status]
        if lease.owner.kind is not expected_owner:
            raise ValueError("intervention status and control-lease owner disagree")
        active = intervention.status in {
            InterventionStatus.OPEN,
            InterventionStatus.CLAIMED,
            InterventionStatus.RESUMING,
        }
        if active and lease.intervention_id != intervention.id:
            raise ValueError("active intervention is not bound to its control lease")
        if not active and lease.intervention_id is not None:
            raise ValueError("terminal intervention cannot retain a control-lease binding")
        if intervention.status is InterventionStatus.CLAIMED and (
            lease.owner.principal_id != intervention.operator_id
        ):
            raise ValueError("claimed intervention and human lease have different operators")


@dataclass(frozen=True, slots=True)
class InMemoryInterventionTransitionRepository:
    """Makes paired state visible atomically while retaining focused repositories."""

    interventions: InMemoryInterventionRouter
    leases: InMemoryControlLeaseRepository

    def get(self, intervention_id: str) -> tuple[Intervention, ControlLease]:
        with self.interventions._lock, self.leases._lock:
            intervention = self._intervention(intervention_id)
            lease = self._lease(str(intervention.session_id))
            InterventionTransition(intervention, lease)
            return intervention, lease

    def list_active(
        self, run_mode: InterventionRunMode | None = None
    ) -> tuple[tuple[Intervention, ControlLease], ...]:
        active = {
            InterventionStatus.OPEN,
            InterventionStatus.CLAIMED,
            InterventionStatus.RESUMING,
        }
        with self.interventions._lock, self.leases._lock:
            interventions = sorted(
                (
                    item
                    for item in self.interventions._interventions.values()
                    if item.status in active
                    and (run_mode is None or item.context.run_mode is run_mode)
                ),
                key=lambda item: item.created_at,
            )
            pairs = tuple((item, self._lease(str(item.session_id))) for item in interventions)
            for pair in pairs:
                InterventionTransition(*pair)
            return pairs

    def create_paused(
        self,
        intervention: Intervention,
        observation: NormalizedObservation,
        expected_lease_version: int,
        expected_lease_owner: ControlOwner,
        replacement_lease: ControlLease,
    ) -> tuple[Intervention, ControlLease]:
        intervention_id = str(intervention.id)
        session_id = str(intervention.session_id)
        with self.interventions._lock, self.leases._lock:
            if intervention_id in self.interventions._interventions:
                raise InterventionConflictError("intervention identity is already routed")
            current_lease = self._lease(session_id)
            if (
                current_lease.version != expected_lease_version
                or current_lease.owner != expected_lease_owner
            ):
                raise LeaseConflictError("control lease version or owner is stale")
            if observation.session_id != intervention.session_id:
                raise ValueError("observation does not belong to the intervention session")
            self._validate_lease_replacement(current_lease, replacement_lease)
            transition = InterventionTransition(intervention, replacement_lease)
            self.interventions._interventions[intervention_id] = intervention
            self.interventions._observations[intervention_id] = observation
            self.leases._leases[session_id] = replacement_lease
            return transition.intervention, transition.lease

    def compare_and_swap(
        self,
        intervention_id: str,
        expected_status: InterventionStatus,
        expected_lease_version: int,
        expected_lease_owner: ControlOwner,
        replacement_intervention: Intervention,
        replacement_lease: ControlLease,
    ) -> tuple[Intervention, ControlLease]:
        with self.interventions._lock, self.leases._lock:
            current_intervention = self._intervention(intervention_id)
            current_lease = self._lease(str(current_intervention.session_id))
            if current_intervention.status is not expected_status:
                raise InterventionConflictError("intervention status is stale")
            if (
                current_lease.version != expected_lease_version
                or current_lease.owner != expected_lease_owner
            ):
                raise LeaseConflictError("control lease version or owner is stale")
            self._validate_intervention_replacement(current_intervention, replacement_intervention)
            self._validate_lease_replacement(current_lease, replacement_lease)
            transition = InterventionTransition(replacement_intervention, replacement_lease)
            self.interventions._interventions[intervention_id] = transition.intervention
            self.leases._leases[str(current_lease.session_id)] = transition.lease
            return transition.intervention, transition.lease

    def _intervention(self, intervention_id: str) -> Intervention:
        try:
            return self.interventions._interventions[intervention_id]
        except KeyError as error:
            raise InterventionNotFoundError(intervention_id) from error

    def _lease(self, session_id: str) -> ControlLease:
        try:
            return self.leases._leases[session_id]
        except KeyError as error:
            raise LeaseNotFoundError(session_id) from error

    @staticmethod
    def _validate_intervention_replacement(
        current: Intervention, replacement: Intervention
    ) -> None:
        if (
            replacement.id != current.id
            or replacement.run_id != current.run_id
            or replacement.session_id != current.session_id
            or replacement.created_at != current.created_at
            or replacement.trigger_code != current.trigger_code
            or replacement.context != current.context
        ):
            raise ValueError("intervention replacement cannot change immutable identity")

    @staticmethod
    def _validate_lease_replacement(current: ControlLease, replacement: ControlLease) -> None:
        if replacement.session_id != current.session_id:
            raise ValueError("replacement lease cannot change session identity")
        if replacement == current:
            return
        if replacement.version != current.version + 1:
            raise ValueError("replacement lease version must increment exactly once")
