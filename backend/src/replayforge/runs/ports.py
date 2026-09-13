"""Ports for durable structured run events and intervention routing."""

from __future__ import annotations

from typing import Protocol

from replayforge.evidence.models import EvidenceRecord, RetentionClass, SanitizedEvidence
from replayforge.interventions.models import InterventionContext
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

    def attach_sanitized(
        self,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord: ...


class InterventionRouter(Protocol):
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
    ) -> str: ...
