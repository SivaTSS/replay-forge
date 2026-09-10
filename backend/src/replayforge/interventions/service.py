"""Lease-coordinated intervention lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.interventions.leases import ControlLeaseService
from replayforge.interventions.models import (
    PAUSED_OWNER,
    ControlLease,
    ControlOwner,
    Intervention,
    InterventionStatus,
    OwnerKind,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.runs.results import RunResult


class InterventionAuthorizationError(ValueError):
    """The operator is not authorized for the requested intervention transition."""


@dataclass(frozen=True, slots=True)
class InterventionTransition:
    intervention: Intervention
    lease: ControlLease


@dataclass(frozen=True, slots=True)
class InterventionResume:
    transition: InterventionTransition
    result: RunResult | None = None


@dataclass(frozen=True, slots=True)
class InterventionCoordinator:
    interventions: InMemoryInterventionRouter
    leases: ControlLeaseService

    def get(self, intervention_id: str) -> InterventionTransition:
        intervention = self.interventions.get(intervention_id)
        lease = self.leases.repository.get(str(intervention.session_id))
        return InterventionTransition(intervention, lease)

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        replacement = current.claim(operator_id)
        lease = self.leases.claim(
            str(current.session_id), expected_lease_version, intervention_id, operator_id
        )
        updated = self.interventions.compare_and_swap(
            intervention_id, InterventionStatus.OPEN, replacement
        )
        return InterventionTransition(updated, lease)

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        if current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        replacement = current.release()
        lease = self.leases.release(str(current.session_id), expected_lease_version, operator_id)
        updated = self.interventions.compare_and_swap(
            intervention_id, InterventionStatus.CLAIMED, replacement
        )
        return InterventionTransition(updated, lease)

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        if current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        replacement = current.begin_resume()
        lease = self.leases.begin_resume(
            str(current.session_id), expected_lease_version, operator_id
        )
        updated = self.interventions.compare_and_swap(
            intervention_id, InterventionStatus.CLAIMED, replacement
        )
        return InterventionTransition(updated, lease)

    def reopen(self, intervention_id: str, explanation: str) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        replacement = current.reopen(explanation)
        updated = self.interventions.compare_and_swap(
            intervention_id, InterventionStatus.RESUMING, replacement
        )
        lease = self.leases.repository.get(str(current.session_id))
        if lease.owner != PAUSED_OWNER:
            raise RuntimeError("a reopened intervention must retain paused ownership")
        return InterventionTransition(updated, lease)

    def complete_resume(
        self, intervention_id: str, expected_lease_version: int, resolution: str
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        replacement = current.resolve(resolution)
        lease = self.leases.complete_resume(str(current.session_id), expected_lease_version)
        updated = self.interventions.compare_and_swap(
            intervention_id, InterventionStatus.RESUMING, replacement
        )
        return InterventionTransition(updated, lease)

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
        if current.status is not InterventionStatus.CLAIMED or current.operator_id != operator_id:
            raise InterventionAuthorizationError("operator does not own this intervention")
        lease = self.leases.heartbeat(
            str(current.session_id),
            expected_lease_version,
            ControlOwner(OwnerKind.HUMAN, operator_id),
        )
        return InterventionTransition(current, lease)

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition:
        current = self.interventions.get(intervention_id)
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
        lease = self.leases.terminate(
            str(current.session_id), expected_lease_version, expected_owner
        )
        updated = self.interventions.compare_and_swap(intervention_id, current.status, replacement)
        return InterventionTransition(updated, lease)
