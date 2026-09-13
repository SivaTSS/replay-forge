"""Thread-safe local intervention router preserving reserved identities."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from replayforge.interventions.models import (
    Intervention,
    InterventionContext,
    InterventionRunMode,
    InterventionStatus,
)
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityKind, parse_id
from replayforge.surfaces.models import NormalizedObservation

if TYPE_CHECKING:
    from replayforge.interventions.leases import ControlLeaseService


class InterventionConflictError(RuntimeError):
    """The reserved intervention identity was already routed."""


class InterventionNotFoundError(KeyError):
    """The intervention identity is unknown."""


@dataclass(slots=True)
class InMemoryInterventionRouter:
    clock: Clock
    lease_service: ControlLeaseService
    _interventions: dict[str, Intervention] = field(init=False, default_factory=dict)
    _observations: dict[str, NormalizedObservation] = field(init=False, default_factory=dict)
    _lock: Lock = field(init=False, default_factory=Lock)

    def open(
        self,
        *,
        intervention_id: str,
        run_id: str,
        session_id: str,
        expected_lease_version: int,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
        context: InterventionContext,
        explanation: str | None = None,
    ) -> str:
        """Create an intervention and pause its lease in one atomic state change."""
        from replayforge.interventions.leases import InMemoryControlLeaseRepository
        from replayforge.interventions.models import AUTOMATION_OWNER, PAUSED_OWNER
        from replayforge.interventions.transactions import (
            InMemoryInterventionTransitionRepository,
        )

        if not isinstance(self.lease_service.repository, InMemoryControlLeaseRepository):
            raise TypeError("opening an intervention requires an atomic transition repository")
        parsed_intervention_id = parse_id(intervention_id, EntityKind.INTERVENTION)
        parsed_run_id = parse_id(run_id, EntityKind.RUN)
        parsed_session_id = parse_id(session_id, EntityKind.SESSION)
        if observation.session_id != parsed_session_id:
            raise ValueError("observation does not belong to the intervention session")
        safe_explanation = explanation or (
            f"Automation paused safely at {step_id}." if step_id else "Automation paused safely."
        )
        intervention = Intervention(
            id=parsed_intervention_id,
            run_id=parsed_run_id,
            session_id=parsed_session_id,
            trigger_code=code,
            explanation=safe_explanation,
            status=InterventionStatus.OPEN,
            created_at=self.clock.now(),
            context=context,
        )
        current_lease = self.lease_service.repository.get(session_id)
        replacement_lease = self.lease_service.prepare_transfer(
            current_lease,
            expected_lease_version,
            AUTOMATION_OWNER,
            PAUSED_OWNER,
            parsed_intervention_id,
        )
        repository = InMemoryInterventionTransitionRepository(self, self.lease_service.repository)
        repository.create_paused(
            intervention,
            observation,
            expected_lease_version,
            AUTOMATION_OWNER,
            replacement_lease,
        )
        return intervention_id

    def get(self, intervention_id: str) -> Intervention:
        with self._lock:
            try:
                return self._interventions[intervention_id]
            except KeyError as error:
                raise InterventionNotFoundError(intervention_id) from error

    def observation(self, intervention_id: str) -> NormalizedObservation:
        with self._lock:
            try:
                return self._observations[intervention_id]
            except KeyError as error:
                raise InterventionNotFoundError(intervention_id) from error

    def list_open(self) -> tuple[Intervention, ...]:
        with self._lock:
            return tuple(
                intervention
                for intervention in self._interventions.values()
                if intervention.status is InterventionStatus.OPEN
            )

    def list_active(self, run_mode: InterventionRunMode | None = None) -> tuple[Intervention, ...]:
        active = {
            InterventionStatus.OPEN,
            InterventionStatus.CLAIMED,
            InterventionStatus.RESUMING,
        }
        with self._lock:
            matches = (
                intervention
                for intervention in self._interventions.values()
                if intervention.status in active
                and (run_mode is None or intervention.context.run_mode is run_mode)
            )
            return tuple(sorted(matches, key=lambda item: item.created_at))
