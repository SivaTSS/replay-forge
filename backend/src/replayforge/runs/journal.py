"""Thread-safe structured run journal with redaction before retention."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import cast

from pydantic import JsonValue

from replayforge.evidence.diagnostics import diagnostic_archive
from replayforge.evidence.models import (
    MAX_ATTACHMENT_BYTES,
    MAX_EVENT_BYTES,
    MAX_MANIFEST_BYTES,
    EventEvidence,
    EvidenceRecord,
    ManifestEntry,
    RetentionClass,
    RunEvidenceManifest,
    SanitizedEvidence,
)
from replayforge.evidence.ports import EvidenceStore
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.policy.types import DataClassification
from replayforge.runs.privacy import event_detail_classifications, terminal_classifications
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
    details: dict[str, JsonValue]


@dataclass(slots=True)
class InMemoryRunJournal:
    """Retain redacted events and optionally publish immutable evidence snapshots."""

    run_id: str
    clock: Clock
    redactor: StructuredRedactor = field(default_factory=StructuredRedactor)
    evidence_store: EvidenceStore | None = None
    event_sink: Callable[[dict[str, object]], None] | None = None
    _events: list[RunEvent] = field(init=False, default_factory=list)
    _evidence_records: list[EvidenceRecord] = field(init=False, default_factory=list)
    _attachments: list[EvidenceRecord] = field(init=False, default_factory=list)
    _terminal_result: EvidenceRecord | None = field(init=False, default=None)
    _manifest_key: str | None = field(init=False, default=None)
    _finalized: bool = field(init=False, default=False)
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
        event_details = details or {}
        sanitized = self.redactor.sanitize_json(
            event_details, event_detail_classifications(event_details), run_salt=self.run_id
        )
        cleaned = json.loads(sanitized.content)
        if not isinstance(cleaned, dict):
            raise RuntimeError("structured redactor must preserve mapping shape")
        with self._lock:
            if self._finalized:
                raise RuntimeError("cannot record an event after run finalization")
            if len(self._events) >= 10_000:
                raise ValueError("run journal exceeds the event-count limit")
            event = RunEvent(
                id=new_id(EntityKind.EVENT),
                run_id=parse_id(run_id, EntityKind.RUN),
                sequence=len(self._events) + 1,
                event_type=event_type,
                occurred_at=self.clock.now(),
                step_id=step_id,
                details=cast(dict[str, JsonValue], cleaned),
            )
            if self.evidence_store is not None:
                event_payload = _event_payload(event, sanitized.redaction_directives)
                if len(event_payload.content) > MAX_EVENT_BYTES:
                    raise ValueError("run event exceeds the evidence verification limit")
                evidence_record = self.evidence_store.write(
                    run_id,
                    f"run-event-{event.sequence:06d}",
                    event_payload,
                    _retention_class(event_type),
                )
                manifest_record = self.evidence_store.write(
                    run_id,
                    "manifest",
                    _manifest_payload(
                        run_id,
                        event.occurred_at,
                        (*self._evidence_records, evidence_record),
                        tuple(self._attachments),
                    ),
                    RetentionClass.OPERATIONAL,
                )
                self._evidence_records.append(evidence_record)
                self._manifest_key = manifest_record.key
            self._events.append(event)
        if self.event_sink is not None:
            with suppress(Exception):  # Viewing cannot invalidate durable audit recording.
                self.event_sink(
                    {
                        "event_type": event.event_type,
                        "step_id": event.step_id,
                        "occurred_at": event.occurred_at.isoformat(),
                        "details": deepcopy(event.details),
                    }
                )

    def attach_sanitized(
        self,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord:
        """Persist already-sanitized binary evidence and publish a new manifest snapshot."""

        if payload.media_type not in {"image/png", "application/zip"}:
            raise ValueError("run attachments must be PNG images or ZIP archives")
        if not payload.content:
            raise ValueError("run attachments cannot be empty")
        if len(payload.content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("run attachment exceeds the twenty-megabyte limit")
        if payload.media_type == "image/png" and not payload.content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise ValueError("PNG run attachment has an invalid signature")
        if payload.media_type == "application/zip" and not payload.content.startswith(b"PK"):
            raise ValueError("ZIP run attachment has an invalid signature")
        with self._lock:
            if self._finalized:
                raise RuntimeError("cannot attach evidence after run finalization")
            if self.evidence_store is None:
                raise RuntimeError("run attachments require an evidence store")
            if len(self._attachments) >= 100:
                raise ValueError("run journal exceeds the attachment-count limit")
            attachment = self.evidence_store.write(
                self.run_id,
                kind,
                payload,
                retention_class,
            )
            manifest = self.evidence_store.write(
                self.run_id,
                "manifest",
                _manifest_payload(
                    self.run_id,
                    self.clock.now(),
                    tuple(self._evidence_records),
                    (*self._attachments, attachment),
                ),
                RetentionClass.OPERATIONAL,
            )
            self._attachments.append(attachment)
            self._manifest_key = manifest.key
            return attachment

    def events(self) -> tuple[RunEvent, ...]:
        with self._lock:
            return deepcopy(tuple(self._events))

    def finalize(
        self,
        result: dict[str, object],
        classifications: dict[str, DataClassification] | None = None,
    ) -> str:
        """Persist one terminal result and return its complete manifest snapshot."""

        if result.get("run_id") != self.run_id:
            raise ValueError("terminal result belongs to a different run")
        if result.get("status") not in {"success", "business_outcome", "failure"}:
            raise ValueError("only completed runs may be finalized")
        sanitized = self.redactor.sanitize_json(
            result, terminal_classifications(result, classifications or {}), run_salt=self.run_id
        )
        if len(sanitized.content) > MAX_EVENT_BYTES:
            raise ValueError("terminal result exceeds the evidence verification limit")
        with self._lock:
            if self._finalized:
                raise RuntimeError("run evidence has already been finalized")
            if self.evidence_store is None:
                self._finalized = True
                return f"evidence://{self.run_id}/manifest.json"
            diagnostics = [e for e in self._events if e.event_type == "execution_diagnostic"]
            if diagnostics:
                trace_status = "unavailable"
                try:
                    if len(self._attachments) >= 100 or any(
                        a.media_type == "application/zip" for a in self._attachments
                    ):
                        raise ValueError("diagnostic attachment slot unavailable")
                    payload = diagnostic_archive(
                        self.run_id, dict(diagnostics[-1].details), self.redactor
                    )
                    self._attachments.append(
                        self.evidence_store.write(
                            self.run_id, "execution-diagnostic", payload, RetentionClass.FAILURE
                        )
                    )
                    trace_status = "captured"
                except (OSError, RuntimeError, ValueError):
                    # Optional diagnostics cannot change an already determined task result.
                    pass
                enriched = {**result, "diagnostic_trace": trace_status}
                sanitized = self.redactor.sanitize_json(
                    enriched,
                    terminal_classifications(enriched, classifications or {}),
                    run_salt=self.run_id,
                )
            retention = (
                RetentionClass.FAILURE
                if result["status"] == "failure"
                else RetentionClass.OPERATIONAL
            )
            terminal_result = self.evidence_store.write(
                self.run_id, "terminal-result", sanitized, retention
            )
            manifest = self.evidence_store.write(
                self.run_id,
                "manifest",
                _manifest_payload(
                    self.run_id,
                    self.clock.now(),
                    tuple(self._evidence_records),
                    tuple(self._attachments),
                    terminal_result,
                ),
                RetentionClass.OPERATIONAL,
            )
            self._terminal_result = terminal_result
            self._manifest_key = manifest.key
            self._finalized = True
            return manifest.key


def _event_payload(event: RunEvent, redaction_directives: tuple[str, ...]) -> SanitizedEvidence:
    payload = EventEvidence(
        details=event.details,
        event_id=str(event.id),
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        run_id=str(event.run_id),
        sequence=event.sequence,
        step_id=event.step_id,
    )
    content = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return SanitizedEvidence(content, "application/json", redaction_directives)


def _manifest_payload(
    run_id: str,
    generated_at: datetime,
    records: tuple[EvidenceRecord, ...],
    attachments: tuple[EvidenceRecord, ...] = (),
    terminal_result: EvidenceRecord | None = None,
) -> SanitizedEvidence:
    manifest = RunEvidenceManifest(
        events=tuple(_manifest_record(record) for record in records),
        attachments=tuple(_manifest_record(record) for record in attachments),
        generated_at=generated_at,
        run_id=run_id,
        terminal_result=_manifest_record(terminal_result) if terminal_result is not None else None,
    )
    content = json.dumps(
        manifest.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
    ).encode()
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("run manifest exceeds the evidence verification limit")
    all_records = (
        *records,
        *attachments,
        *((terminal_result,) if terminal_result is not None else ()),
    )
    directives = tuple(
        sorted({directive for record in all_records for directive in record.redaction_directives})
    )
    return SanitizedEvidence(content, "application/json", directives)


def _manifest_record(record: EvidenceRecord) -> ManifestEntry:
    return ManifestEntry(
        content_hash=record.content_hash,
        created_at=record.created_at,
        evidence_id=str(record.id),
        key=record.key,
        media_type=record.media_type,
        redaction_directives=record.redaction_directives,
        retention_class=record.retention_class,
        size_bytes=record.size_bytes,
    )


def _retention_class(event_type: str) -> RetentionClass:
    if event_type.endswith("_failed"):
        return RetentionClass.FAILURE
    if event_type == "intervention_required" or event_type.startswith("human_input_"):
        return RetentionClass.HUMAN_AUDIT
    return RetentionClass.OPERATIONAL


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
