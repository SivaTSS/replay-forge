"""Evidence persistence port owned by the domain."""

from __future__ import annotations

from typing import Protocol

from replayforge.evidence.models import EvidenceRecord, RetentionClass, SanitizedEvidence


class EvidenceStore(Protocol):
    def write(
        self,
        run_id: str,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord: ...

    def read(self, key: str) -> bytes: ...
