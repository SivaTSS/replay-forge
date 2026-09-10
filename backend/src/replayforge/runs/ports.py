"""Ports for durable structured run events and intervention routing."""

from __future__ import annotations

from typing import Protocol

from replayforge.surfaces.models import NormalizedObservation


class RunRecorder(Protocol):
    @property
    def evidence_manifest_key(self) -> str: ...

    def record(
        self,
        event_type: str,
        run_id: str,
        *,
        step_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None: ...


class InterventionRouter(Protocol):
    def create(
        self,
        *,
        intervention_id: str,
        run_id: str,
        session_id: str,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
    ) -> str: ...
