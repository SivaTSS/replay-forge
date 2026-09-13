"""Evidence values that cross the persistence trust boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from replayforge.shared.ids import EntityId, EntityKind, parse_id

MAX_MANIFEST_BYTES = 2_000_000
MAX_EVENT_BYTES = 2_000_000
MAX_ATTACHMENT_BYTES = 20_000_000
SUPPORTED_EVIDENCE_MEDIA_TYPES = frozenset({"application/json", "image/png", "application/zip"})
EvidenceMediaType = Literal["application/json", "image/png", "application/zip"]


class RetentionClass(StrEnum):
    OPERATIONAL = "operational"
    FAILURE = "failure"
    HUMAN_AUDIT = "human_audit"
    PROVIDER_METADATA = "provider_metadata"


@dataclass(frozen=True, slots=True)
class SanitizedEvidence:
    """Bytes that have passed redaction and the forbidden-content scanner."""

    content: bytes
    media_type: EvidenceMediaType
    redaction_directives: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("sanitized evidence cannot be empty")
        if self.media_type not in SUPPORTED_EVIDENCE_MEDIA_TYPES:
            raise ValueError("sanitized evidence has an unsupported media type")
        if len(set(self.redaction_directives)) != len(self.redaction_directives):
            raise ValueError("redaction directives must be unique")
        if any(not directive for directive in self.redaction_directives):
            raise ValueError("redaction directives cannot be empty")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: EntityId
    key: str
    media_type: EvidenceMediaType
    size_bytes: int
    content_hash: str
    retention_class: RetentionClass
    redaction_directives: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        parse_id(str(self.id), EntityKind.EVIDENCE)
        if (
            not self.key.startswith("evidence://")
            or self.key == "evidence://"
            or any(character.isspace() for character in self.key)
        ):
            raise ValueError("evidence record requires an opaque evidence key")
        if self.media_type not in SUPPORTED_EVIDENCE_MEDIA_TYPES:
            raise ValueError("evidence record has an unsupported media type")
        if not 0 < self.size_bytes <= MAX_ATTACHMENT_BYTES:
            raise ValueError("evidence record size is outside the supported range")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash) is None:
            raise ValueError("evidence record requires a SHA-256 content hash")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("evidence timestamp must include an offset")
        if len(set(self.redaction_directives)) != len(self.redaction_directives) or any(
            not directive for directive in self.redaction_directives
        ):
            raise ValueError("redaction directives must be unique non-empty values")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestEntry(EvidenceModel):
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
    evidence_id: str
    key: str = Field(pattern=r"^evidence://[^\s]+$")
    media_type: EvidenceMediaType
    redaction_directives: tuple[str, ...]
    retention_class: RetentionClass
    size_bytes: int = Field(gt=0, le=MAX_ATTACHMENT_BYTES)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        parse_id(value, EntityKind.EVIDENCE)
        return value

    @field_validator("created_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence timestamp must include an offset")
        return value

    @field_validator("redaction_directives")
    @classmethod
    def validate_directives(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not directive for directive in value):
            raise ValueError("redaction directives must be unique non-empty values")
        return value


class RunEvidenceManifest(EvidenceModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    generated_at: datetime
    events: tuple[ManifestEntry, ...] = Field(min_length=1, max_length=10_000)
    attachments: tuple[ManifestEntry, ...] = Field(default=(), max_length=100)
    terminal_result: ManifestEntry | None = None

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

    @model_validator(mode="after")
    def validate_relationships(self) -> RunEvidenceManifest:
        entries = (
            *self.events,
            *self.attachments,
            *((self.terminal_result,) if self.terminal_result is not None else ()),
        )
        keys = tuple(entry.key for entry in entries)
        if len(set(keys)) != len(keys):
            raise ValueError("evidence manifest keys must be unique")
        evidence_ids = tuple(entry.evidence_id for entry in entries)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence manifest identities must be unique")
        prefix = f"evidence://{self.run_id}/"
        if any(not key.startswith(prefix) for key in keys):
            raise ValueError("evidence manifest entries must belong to the manifest run")
        if any(entry.media_type != "application/json" for entry in self.events):
            raise ValueError("run events must contain structured JSON evidence")
        if any(entry.media_type == "application/json" for entry in self.attachments):
            raise ValueError("run attachments cannot contain structured event evidence")
        if (
            self.terminal_result is not None
            and self.terminal_result.media_type != "application/json"
        ):
            raise ValueError("terminal result evidence must contain structured JSON")
        return self


class EventEvidence(EvidenceModel):
    details: dict[str, JsonValue]
    event_id: str
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    occurred_at: datetime
    run_id: str
    sequence: int = Field(ge=1)
    step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]+$")

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        parse_id(value, EntityKind.EVENT)
        return value

    @field_validator("run_id")
    @classmethod
    def validate_event_run_id(cls, value: str) -> str:
        parse_id(value, EntityKind.RUN)
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_event_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must include an offset")
        return value
