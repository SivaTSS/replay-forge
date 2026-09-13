"""Thread-safe local intervention router preserving reserved identities."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from replayforge.interventions.models import (
    Intervention,
    InterventionContext,
    InterventionRunMode,
    InterventionStatus,
)
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityKind, parse_id
from replayforge.surfaces.models import NormalizedObservation


class InterventionConflictError(RuntimeError):
    """The reserved intervention identity was already routed."""


class InterventionNotFoundError(KeyError):
    """The intervention identity is unknown."""


@dataclass(slots=True)
class InMemoryInterventionRouter:
    clock: Clock
    _interventions: dict[str, Intervention] = field(init=False, default_factory=dict)
    _observations: dict[str, NormalizedObservation] = field(init=False, default_factory=dict)
    _lock: Lock = field(init=False, default_factory=Lock)

    def create(
        self,
        *,
        intervention_id: str,
        run_id: str,
        session_id: str,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
        context: InterventionContext,
        explanation: str | None = None,
    ) -> str:
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
        with self._lock:
            if intervention_id in self._interventions:
                raise InterventionConflictError("intervention identity is already routed")
            self._interventions[intervention_id] = intervention
            self._observations[intervention_id] = observation
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

    def compare_and_swap(
        self,
        intervention_id: str,
        expected_status: InterventionStatus,
        replacement: Intervention,
    ) -> Intervention:
        with self._lock:
            current = self._interventions.get(intervention_id)
            if current is None:
                raise InterventionNotFoundError(intervention_id)
            if current.status is not expected_status:
                raise InterventionConflictError("intervention status is stale")
            if (
                replacement.id != current.id
                or replacement.run_id != current.run_id
                or replacement.session_id != current.session_id
                or replacement.created_at != current.created_at
                or replacement.trigger_code != current.trigger_code
                or replacement.context != current.context
            ):
                raise ValueError("intervention replacement cannot change immutable identity")
            self._interventions[intervention_id] = replacement
            return replacement
