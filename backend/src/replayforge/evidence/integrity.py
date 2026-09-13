"""Independent validation for persisted run-evidence manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from replayforge.evidence.models import (
    MAX_EVENT_BYTES,
    MAX_MANIFEST_BYTES,
    EventEvidence,
    ManifestEntry,
    RetentionClass,
    RunEvidenceManifest,
)
from replayforge.evidence.ports import EvidenceStore
from replayforge.shared.ids import EntityKind, parse_id


class EvidenceIntegrityError(ValueError):
    """Raised when retained evidence cannot prove its declared integrity."""


class TerminalResultEvidence(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    status: Literal["success", "business_outcome", "failure"]
    run_id: str

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        parse_id(value, EntityKind.RUN)
        return value


@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    manifest_key: str
    manifest_hash: str
    run_id: str
    event_count: int
    attachment_count: int
    terminal_result_verified: bool


def verify_run_manifest(
    store: EvidenceStore, manifest_key: str, *, require_terminal: bool = True
) -> EvidenceVerification:
    """Verify the manifest schema and every referenced event payload."""

    manifest_content = store.read(manifest_key)
    if len(manifest_content) > MAX_MANIFEST_BYTES:
        raise EvidenceIntegrityError("evidence manifest exceeds the verification limit")
    manifest = _parse_manifest(manifest_content)
    expected_prefix = f"evidence://{manifest.run_id}/"
    if not manifest_key.startswith(expected_prefix):
        raise EvidenceIntegrityError("manifest key belongs to a different run")
    seen_keys: set[str] = set()

    for expected_sequence, entry in enumerate(manifest.events, start=1):
        if entry.key in seen_keys:
            raise EvidenceIntegrityError("evidence manifest contains a duplicate key")
        seen_keys.add(entry.key)
        if not entry.key.startswith(expected_prefix):
            raise EvidenceIntegrityError("evidence entry belongs to a different run")
        content = _verified_content(store, entry)
        event = _parse_event(content)
        if event.run_id != manifest.run_id:
            raise EvidenceIntegrityError("event payload belongs to a different run")
        if event.sequence != expected_sequence:
            raise EvidenceIntegrityError("event sequence is not contiguous and ordered")

    for attachment in manifest.attachments:
        if attachment.key in seen_keys:
            raise EvidenceIntegrityError("evidence manifest contains a duplicate key")
        seen_keys.add(attachment.key)
        if not attachment.key.startswith(expected_prefix):
            raise EvidenceIntegrityError("evidence attachment belongs to a different run")
        content = _verified_content(store, attachment)
        if attachment.media_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EvidenceIntegrityError("PNG evidence has an invalid signature")
        if attachment.media_type == "application/zip" and not content.startswith(b"PK"):
            raise EvidenceIntegrityError("ZIP evidence has an invalid signature")
        if attachment.media_type == "application/json":
            raise EvidenceIntegrityError("structured events cannot be declared as attachments")

    terminal_verified = manifest.terminal_result is not None
    if manifest.terminal_result is None:
        if require_terminal:
            raise EvidenceIntegrityError("completed-run manifest has no terminal result")
    else:
        terminal = manifest.terminal_result
        if terminal.key in seen_keys:
            raise EvidenceIntegrityError("terminal result duplicates an event key")
        if not terminal.key.startswith(expected_prefix):
            raise EvidenceIntegrityError("terminal result belongs to a different run")
        result = _parse_terminal_result(_verified_content(store, terminal))
        if result.run_id != manifest.run_id:
            raise EvidenceIntegrityError("terminal result payload belongs to a different run")
        expected_retention = (
            RetentionClass.FAILURE if result.status == "failure" else RetentionClass.OPERATIONAL
        )
        if terminal.retention_class is not expected_retention:
            raise EvidenceIntegrityError("terminal result has the wrong retention class")

    return EvidenceVerification(
        manifest_key=manifest_key,
        manifest_hash=f"sha256:{hashlib.sha256(manifest_content).hexdigest()}",
        run_id=manifest.run_id,
        event_count=len(manifest.events),
        attachment_count=len(manifest.attachments),
        terminal_result_verified=terminal_verified,
    )


def _verified_content(store: EvidenceStore, entry: ManifestEntry) -> bytes:
    parse_id(entry.evidence_id, EntityKind.EVIDENCE)
    content = store.read(entry.key)
    if len(content) != entry.size_bytes:
        raise EvidenceIntegrityError("evidence size does not match its manifest entry")
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if digest != entry.content_hash:
        raise EvidenceIntegrityError("evidence hash does not match its manifest entry")
    return content


def _parse_manifest(content: bytes) -> RunEvidenceManifest:
    try:
        return RunEvidenceManifest.model_validate_json(content)
    except ValidationError as error:
        raise EvidenceIntegrityError("evidence manifest violates its schema") from error


def _parse_event(content: bytes) -> EventEvidence:
    if len(content) > MAX_EVENT_BYTES:
        raise EvidenceIntegrityError("event evidence exceeds the verification limit")
    try:
        return EventEvidence.model_validate_json(content)
    except ValidationError as error:
        raise EvidenceIntegrityError("event evidence violates its schema") from error


def _parse_terminal_result(content: bytes) -> TerminalResultEvidence:
    if len(content) > MAX_EVENT_BYTES:
        raise EvidenceIntegrityError("terminal result evidence exceeds the verification limit")
    try:
        return TerminalResultEvidence.model_validate_json(content)
    except ValidationError as error:
        raise EvidenceIntegrityError("terminal result evidence violates its schema") from error
