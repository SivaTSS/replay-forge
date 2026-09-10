"""Thread-safe structured run journal with redaction before retention."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from replayforge.evidence.models import EvidenceRecord, RetentionClass, SanitizedEvidence
from replayforge.evidence.ports import EvidenceStore
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityId, EntityKind, new_id, parse_id

_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_STEP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class RunEvent:
    id: EntityId
    run_id: EntityId
    sequence: int
    event_type: str
    occurred_at: datetime
    step_id: str | None
    details: dict[str, object]


@dataclass(slots=True)
class InMemoryRunJournal:
    """Retain redacted events and optionally publish immutable evidence snapshots."""

    run_id: str
    clock: Clock
    redactor: StructuredRedactor = field(default_factory=StructuredRedactor)
    evidence_store: EvidenceStore | None = None
    _events: list[RunEvent] = field(init=False, default_factory=list)
    _evidence_records: list[EvidenceRecord] = field(init=False, default_factory=list)
    _manifest_key: str | None = field(init=False, default=None)
    _lock: Lock = field(init=False, default_factory=Lock)

    def __post_init__(self) -> None:
        parse_id(self.run_id, EntityKind.RUN)

    @property
    def evidence_manifest_key(self) -> str:
        with self._lock:
            return self._manifest_key or f"evidence://{self.run_id}/manifest.json"

    def record(
        self,
        event_type: str,
        run_id: str,
        *,
        step_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if run_id != self.run_id:
            raise ValueError("journal cannot record an event for a different run")
        if _EVENT_TYPE_PATTERN.fullmatch(event_type) is None:
            raise ValueError("event type must be a lowercase stable identifier")
        if step_id is not None and _STEP_ID_PATTERN.fullmatch(step_id) is None:
            raise ValueError("step ID is invalid")
        sanitized = self.redactor.sanitize_json(details or {}, {}, run_salt=self.run_id)
        cleaned = json.loads(sanitized.content)
        if not isinstance(cleaned, dict):
            raise RuntimeError("structured redactor must preserve mapping shape")
        with self._lock:
            event = RunEvent(
                id=new_id(EntityKind.EVENT),
                run_id=parse_id(run_id, EntityKind.RUN),
                sequence=len(self._events) + 1,
                event_type=event_type,
                occurred_at=self.clock.now(),
                step_id=step_id,
                details=cleaned,
            )
            if self.evidence_store is not None:
                evidence_record = self.evidence_store.write(
                    run_id,
                    f"run-event-{event.sequence:06d}",
                    _event_payload(event, sanitized.redaction_directives),
                    _retention_class(event_type),
                )
                manifest_record = self.evidence_store.write(
                    run_id,
                    "manifest",
                    _manifest_payload(
                        run_id,
                        event.occurred_at,
                        (*self._evidence_records, evidence_record),
                    ),
                    RetentionClass.OPERATIONAL,
                )
                self._evidence_records.append(evidence_record)
                self._manifest_key = manifest_record.key
            self._events.append(event)

    def events(self) -> tuple[RunEvent, ...]:
        with self._lock:
            return tuple(self._events)


def _event_payload(event: RunEvent, redaction_directives: tuple[str, ...]) -> SanitizedEvidence:
    content = json.dumps(
        {
            "details": event.details,
            "event_id": str(event.id),
            "event_type": event.event_type,
            "occurred_at": _timestamp(event.occurred_at),
            "run_id": str(event.run_id),
            "sequence": event.sequence,
            "step_id": event.step_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return SanitizedEvidence(content, "application/json", redaction_directives)


def _manifest_payload(
    run_id: str, generated_at: datetime, records: tuple[EvidenceRecord, ...]
) -> SanitizedEvidence:
    content = json.dumps(
        {
            "events": [
                {
                    "content_hash": record.content_hash,
                    "created_at": _timestamp(record.created_at),
                    "evidence_id": str(record.id),
                    "key": record.key,
                    "media_type": record.media_type,
                    "redaction_directives": list(record.redaction_directives),
                    "retention_class": record.retention_class.value,
                    "size_bytes": record.size_bytes,
                }
                for record in records
            ],
            "generated_at": _timestamp(generated_at),
            "run_id": run_id,
            "schema_version": "1.0",
        },
        indent=2,
        sort_keys=True,
    ).encode()
    directives = tuple(
        sorted({directive for record in records for directive in record.redaction_directives})
    )
    return SanitizedEvidence(content, "application/json", directives)


def _retention_class(event_type: str) -> RetentionClass:
    if event_type.endswith("_failed"):
        return RetentionClass.FAILURE
    if event_type == "intervention_required":
        return RetentionClass.HUMAN_AUDIT
    return RetentionClass.OPERATIONAL


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
