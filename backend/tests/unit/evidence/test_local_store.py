import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.evidence import local_store
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.models import RetentionClass, SanitizedEvidence
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


@pytest.fixture
def store(tmp_path: Path) -> LocalEvidenceStore:
    return LocalEvidenceStore(
        tmp_path / "evidence",
        FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC)),
    )


def test_local_store_atomically_writes_sanitized_content_and_metadata(
    store: LocalEvidenceStore,
) -> None:
    payload = StructuredRedactor().sanitize_json({"event": "run_started"}, {}, run_salt="test")

    record = store.write(new_id(EntityKind.RUN), "event", payload, RetentionClass.OPERATIONAL)

    assert store.read(record.key) == payload.content
    assert record.content_hash == f"sha256:{hashlib.sha256(payload.content).hexdigest()}"
    assert record.size_bytes == len(payload.content)
    relative = record.key.removeprefix("evidence://")
    metadata_path = (store.root / relative).with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text())
    assert metadata["content_hash"] == record.content_hash
    assert metadata["retention_class"] == "operational"


@pytest.mark.parametrize("kind", ["../escape", "UPPER", "contains space", ""])
def test_store_rejects_unsafe_evidence_kind(store: LocalEvidenceStore, kind: str) -> None:
    payload = StructuredRedactor().sanitize_json({}, {}, run_salt="test")

    with pytest.raises(ValueError, match="safe path segment"):
        store.write(new_id(EntityKind.RUN), kind, payload, RetentionClass.FAILURE)


def test_store_rejects_invalid_run_identifier(store: LocalEvidenceStore) -> None:
    payload = StructuredRedactor().sanitize_json({}, {}, run_salt="test")

    with pytest.raises(ValueError, match="identifier"):
        store.write("not-a-run", "event", payload, RetentionClass.FAILURE)


def test_store_rejects_oversized_payload_before_writing(
    store: LocalEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local_store, "MAX_ATTACHMENT_BYTES", 2)
    payload = SanitizedEvidence(b"{}\n", "application/json", ())

    with pytest.raises(ValueError, match="storage limit"):
        store.write(new_id(EntityKind.RUN), "event", payload, RetentionClass.OPERATIONAL)

    assert not tuple(store.root.rglob("*.bin"))


@pytest.mark.parametrize(
    "key", ["file:///tmp/data", "evidence://../../outside", "evidence:///absolute/path"]
)
def test_read_rejects_invalid_or_escaping_key(store: LocalEvidenceStore, key: str) -> None:
    with pytest.raises(ValueError):
        store.read(key)
