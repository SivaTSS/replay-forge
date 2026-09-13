"""Atomic local evidence adapter using opaque keys and sidecar metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from replayforge.evidence.models import (
    MAX_ATTACHMENT_BYTES,
    EvidenceRecord,
    RetentionClass,
    SanitizedEvidence,
)
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityKind, new_id, parse_id

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True, slots=True)
class LocalEvidenceStore:
    root: Path
    clock: Clock

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        run_id: str,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord:
        parse_id(run_id, EntityKind.RUN)
        if _KIND_PATTERN.fullmatch(kind) is None:
            raise ValueError("evidence kind must be a lowercase safe path segment")
        if len(payload.content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("evidence payload exceeds the storage limit")
        evidence_id = new_id(EntityKind.EVIDENCE)
        relative = Path(run_id) / f"{kind}-{evidence_id}.bin"
        destination = self._resolve_key(relative.as_posix())
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = f"sha256:{hashlib.sha256(payload.content).hexdigest()}"
        created_at = self.clock.now()
        self._atomic_write(destination, payload.content)
        metadata = {
            "content_hash": digest,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "media_type": payload.media_type,
            "redaction_directives": list(payload.redaction_directives),
            "retention_class": retention_class.value,
            "size_bytes": len(payload.content),
        }
        self._atomic_write(
            destination.with_suffix(".metadata.json"),
            (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(),
        )
        return EvidenceRecord(
            id=evidence_id,
            key=f"evidence://{relative.as_posix()}",
            media_type=payload.media_type,
            size_bytes=len(payload.content),
            content_hash=digest,
            retention_class=retention_class,
            redaction_directives=payload.redaction_directives,
            created_at=created_at,
        )

    def read(self, key: str) -> bytes:
        prefix = "evidence://"
        if not key.startswith(prefix):
            raise ValueError("evidence key must use the evidence scheme")
        return self._resolve_key(key.removeprefix(prefix)).read_bytes()

    def _resolve_key(self, relative_key: str) -> Path:
        destination = (self.root / relative_key).resolve()
        root = self.root.resolve()
        if not destination.is_relative_to(root):
            raise ValueError("evidence key escapes the configured root")
        return destination

    @staticmethod
    def _atomic_write(destination: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
