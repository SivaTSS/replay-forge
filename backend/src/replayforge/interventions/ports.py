"""Atomic persistence contract for live-session control leases."""

from __future__ import annotations

from typing import Protocol

from replayforge.interventions.models import ControlLease, ControlOwner


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
