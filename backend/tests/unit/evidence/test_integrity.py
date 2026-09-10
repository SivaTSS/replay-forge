import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.evidence.integrity import EvidenceIntegrityError, verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.models import RetentionClass, SanitizedEvidence
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


@pytest.fixture
def retained_run(tmp_path: Path) -> tuple[LocalEvidenceStore, InMemoryRunJournal]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "evidence", clock)
    journal = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)
    journal.record("replay_started", journal.run_id)
    journal.attach_sanitized(
        "intervention-before",
        SanitizedEvidence(
            b"\x89PNG\r\n\x1a\nmasked-synthetic-frame",
            "image/png",
            ("mask:input",),
        ),
        RetentionClass.HUMAN_AUDIT,
    )
    journal.attach_sanitized(
        "browser-trace",
        SanitizedEvidence(
            b"PK\x03\x04sanitized-synthetic-trace",
            "application/zip",
            ("sanitize:trace",),
        ),
        RetentionClass.OPERATIONAL,
    )
    journal.record("checkpoint_verified", journal.run_id)
    journal.finalize(
        {
            "status": "success",
            "run_id": journal.run_id,
            "outputs": {"balance": "[REDACTED_FINANCIAL]"},
        }
    )
    return store, journal


def test_verifier_proves_manifest_and_all_event_hashes(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
) -> None:
    store, journal = retained_run

    verification = verify_run_manifest(store, journal.evidence_manifest_key)

    assert verification.run_id == journal.run_id
    assert verification.event_count == 2
    assert verification.attachment_count == 2
    assert verification.terminal_result_verified
    assert verification.manifest_hash.startswith("sha256:")


def test_verifier_rejects_tampered_event(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
) -> None:
    store, journal = retained_run
    manifest = json.loads(store.read(journal.evidence_manifest_key))
    event_path = store.root / manifest["events"][0]["key"].removeprefix("evidence://")
    event_path.write_text("{}")

    with pytest.raises(EvidenceIntegrityError, match="size|hash"):
        verify_run_manifest(store, journal.evidence_manifest_key)


def test_verifier_rejects_invalid_attachment_signature(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
) -> None:
    store, journal = retained_run
    manifest_path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(manifest_path.read_text())
    attachment_path = store.root / manifest["attachments"][0]["key"].removeprefix("evidence://")
    content = b"not-a-png"
    attachment_path.write_bytes(content)
    manifest["attachments"][0]["size_bytes"] = len(content)
    manifest["attachments"][0]["content_hash"] = f"sha256:{hashlib.sha256(content).hexdigest()}"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceIntegrityError, match="invalid signature"):
        verify_run_manifest(store, journal.evidence_manifest_key)


@pytest.mark.parametrize("mutation", ["duplicate", "cross_run", "sequence"])
def test_verifier_rejects_invalid_manifest_relationships(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal], mutation: str
) -> None:
    store, journal = retained_run
    manifest_path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(manifest_path.read_text())
    if mutation == "duplicate":
        manifest["events"][1]["key"] = manifest["events"][0]["key"]
    elif mutation == "cross_run":
        other_run = str(new_id(EntityKind.RUN))
        manifest["events"][0]["key"] = manifest["events"][0]["key"].replace(
            journal.run_id, other_run
        )
    else:
        event_path = store.root / manifest["events"][1]["key"].removeprefix("evidence://")
        event = json.loads(event_path.read_text())
        event["sequence"] = 7
        content = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        event_path.write_bytes(content)
        manifest["events"][1]["size_bytes"] = len(content)
        manifest["events"][1]["content_hash"] = f"sha256:{hashlib.sha256(content).hexdigest()}"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceIntegrityError):
        verify_run_manifest(store, journal.evidence_manifest_key)
