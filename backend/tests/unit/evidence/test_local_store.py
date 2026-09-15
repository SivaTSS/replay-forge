import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.evidence import local_store
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.models import RawScreenshot, RetentionClass, SanitizedEvidence
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


def test_store_refuses_to_replace_an_existing_evidence_identity(
    store: LocalEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = new_id(EntityKind.EVIDENCE)
    monkeypatch.setattr(local_store, "new_id", lambda _kind: evidence_id)
    payload = StructuredRedactor().sanitize_json({"event": "started"}, {}, run_salt="test")
    run_id = new_id(EntityKind.RUN)
    store.write(run_id, "event", payload, RetentionClass.OPERATIONAL)

    with pytest.raises(RuntimeError, match="identity collision"):
        store.write(run_id, "event", payload, RetentionClass.OPERATIONAL)

    assert len(tuple(path for path in store.root.rglob("*") if path.is_file())) == 2


def test_raw_screenshot_is_unchanged_private_png_with_honest_metadata(
    store: LocalEvidenceStore,
) -> None:
    raw = b"\x89PNG\r\n\x1a\nunmasked-test-pixels"
    record = store.write(
        str(new_id(EntityKind.RUN)), "failure-state", RawScreenshot(raw), RetentionClass.FAILURE
    )
    path = store.root / record.key.removeprefix("evidence://")
    assert path.suffix == ".png"
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o777 == 0o600
    metadata = json.loads(path.with_suffix(".metadata.json").read_text())
    assert metadata["redaction_directives"] == ["unredacted:raw-screenshot"]
    assert record.content_hash == "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("content", [b"", b"not-png"])
def test_raw_screenshot_rejects_non_png(content: bytes) -> None:
    with pytest.raises(ValueError, match="PNG"):
        RawScreenshot(content)


def test_raw_screenshot_cannot_claim_masking() -> None:
    with pytest.raises(ValueError, match="unredacted"):
        RawScreenshot(b"\x89PNG\r\n\x1a\npixels", redaction_directives=("mask:all",))
