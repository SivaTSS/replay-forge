"""Independent validation for persisted run-evidence manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from replayforge.evidence.models import RetentionClass
from replayforge.evidence.ports import EvidenceStore
from replayforge.shared.ids import EntityKind, parse_id

_MAX_MANIFEST_BYTES = 2_000_000
_MAX_EVENT_BYTES = 2_000_000


class EvidenceIntegrityError(ValueError):
    """Raised when retained evidence cannot prove its declared integrity."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestEntry(_StrictModel):
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
    evidence_id: str
    key: str = Field(pattern=r"^evidence://.+$")
    media_type: Literal["application/json"]
    redaction_directives: tuple[str, ...]
    retention_class: RetentionClass
    size_bytes: int = Field(ge=0, le=_MAX_EVENT_BYTES)

    @field_validator("created_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence timestamp must include an offset")
        return value


class RunEvidenceManifest(_StrictModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    run_id: str
    generated_at: datetime
    events: tuple[ManifestEntry, ...] = Field(min_length=1, max_length=10_000)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        parse_id(value, EntityKind.RUN)
        return value

    @field_validator("generated_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest timestamp must include an offset")
        return value


class EventEvidence(_StrictModel):
    details: dict[str, Any]
    event_id: str
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    occurred_at: datetime
    run_id: str
    sequence: int = Field(ge=1)
    step_id: str | None

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        parse_id(value, EntityKind.EVENT)
        return value

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        parse_id(value, EntityKind.RUN)
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must include an offset")
        return value


@dataclass(frozen=True, slots=True)
class EvidenceVerification:
    manifest_key: str
    manifest_hash: str
    run_id: str
    event_count: int


def verify_run_manifest(store: EvidenceStore, manifest_key: str) -> EvidenceVerification:
    """Verify the manifest schema and every referenced event payload."""

    manifest_content = store.read(manifest_key)
    if len(manifest_content) > _MAX_MANIFEST_BYTES:
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
        parse_id(entry.evidence_id, EntityKind.EVIDENCE)
        content = store.read(entry.key)
        if len(content) != entry.size_bytes:
            raise EvidenceIntegrityError("evidence size does not match its manifest entry")
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != entry.content_hash:
            raise EvidenceIntegrityError("evidence hash does not match its manifest entry")
        event = _parse_event(content)
        if event.run_id != manifest.run_id:
            raise EvidenceIntegrityError("event payload belongs to a different run")
        if event.sequence != expected_sequence:
            raise EvidenceIntegrityError("event sequence is not contiguous and ordered")

    return EvidenceVerification(
        manifest_key=manifest_key,
        manifest_hash=f"sha256:{hashlib.sha256(manifest_content).hexdigest()}",
        run_id=manifest.run_id,
        event_count=len(manifest.events),
    )


def _parse_manifest(content: bytes) -> RunEvidenceManifest:
    try:
        return RunEvidenceManifest.model_validate_json(content)
    except ValidationError as error:
        raise EvidenceIntegrityError("evidence manifest violates its schema") from error


def _parse_event(content: bytes) -> EventEvidence:
    if len(content) > _MAX_EVENT_BYTES:
        raise EvidenceIntegrityError("event evidence exceeds the verification limit")
    try:
        return EventEvidence.model_validate_json(content)
    except ValidationError as error:
        raise EvidenceIntegrityError("event evidence violates its schema") from error
