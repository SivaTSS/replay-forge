"""Evidence values that cross the persistence trust boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from replayforge.shared.ids import EntityId


class RetentionClass(StrEnum):
    OPERATIONAL = "operational"
    FAILURE = "failure"
    HUMAN_AUDIT = "human_audit"
    PROVIDER_METADATA = "provider_metadata"


@dataclass(frozen=True, slots=True)
class SanitizedEvidence:
    """Bytes that have passed redaction and the forbidden-content scanner."""

    content: bytes
    media_type: str
    redaction_directives: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: EntityId
    key: str
    media_type: str
    size_bytes: int
    content_hash: str
    retention_class: RetentionClass
    redaction_directives: tuple[str, ...]
    created_at: datetime
