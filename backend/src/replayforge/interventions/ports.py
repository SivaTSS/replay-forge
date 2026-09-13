"""Atomic persistence contracts for intervention and lease state."""

from __future__ import annotations

from typing import Protocol

from replayforge.interventions.models import (
    ControlLease,
    ControlOwner,
    Intervention,
    InterventionRunMode,
    InterventionStatus,
)
from replayforge.surfaces.models import NormalizedObservation


class ControlLeaseRepository(Protocol):
    def create(self, lease: ControlLease) -> ControlLease: ...

    def get(self, session_id: str) -> ControlLease: ...

    def compare_and_swap(
        self,
        session_id: str,
        expected_version: int,
        expected_owner: ControlOwner,
        replacement: ControlLease,
    ) -> ControlLease: ...


class InterventionTransitionRepository(Protocol):
    """Reads and changes an intervention and its control lease as one state unit."""

    def get(self, intervention_id: str) -> tuple[Intervention, ControlLease]: ...

    def list_active(
        self, run_mode: InterventionRunMode | None = None
    ) -> tuple[tuple[Intervention, ControlLease], ...]: ...

    def create_paused(
        self,
        intervention: Intervention,
        observation: NormalizedObservation,
        expected_lease_version: int,
        expected_lease_owner: ControlOwner,
        replacement_lease: ControlLease,
    ) -> tuple[Intervention, ControlLease]: ...

    def compare_and_swap(
        self,
        intervention_id: str,
        expected_status: InterventionStatus,
        expected_lease_version: int,
        expected_lease_owner: ControlOwner,
        replacement_intervention: Intervention,
        replacement_lease: ControlLease,
    ) -> tuple[Intervention, ControlLease]: ...
