import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


@pytest.mark.parametrize(
    "mutation", ["duplicate_key", "duplicate_identity", "cross_run", "sequence"]
)
def test_verifier_rejects_invalid_manifest_relationships(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal], mutation: str
) -> None:
    store, journal = retained_run
    manifest_path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(manifest_path.read_text())
    if mutation == "duplicate_key":
        manifest["events"][1]["key"] = manifest["events"][0]["key"]
    elif mutation == "duplicate_identity":
        manifest["events"][1]["evidence_id"] = manifest["events"][0]["evidence_id"]
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


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("events", "created_at", "2026-09-10T12:00:00"),
        ("events", "redaction_directives", ["mask:input", "mask:input"]),
        ("events", "redaction_directives", [""]),
        ("events", "media_type", "image/png"),
        ("attachments", "media_type", "application/json"),
        ("terminal_result", "media_type", "image/png"),
        ("terminal_result", "retention_class", "failure"),
    ],
)
def test_manifest_rejects_invalid_retention_and_payload_metadata(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
    section: str,
    field: str,
    value: Any,
) -> None:
    store, journal = retained_run
    path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(path.read_text())
    entry = manifest[section] if section == "terminal_result" else manifest[section][0]
    entry[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvidenceIntegrityError):
        verify_run_manifest(store, journal.evidence_manifest_key)


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("events", "run_id", "run_" + "a" * 32, "event payload belongs"),
        ("events", "occurred_at", "2026-09-10T12:00:00", "event evidence violates"),
        ("events", "sequence", 0, "event evidence violates"),
        ("terminal_result", "run_id", "run_" + "a" * 32, "terminal result payload belongs"),
        ("terminal_result", "status", "intervention_required", "terminal result evidence violates"),
    ],
)
def test_valid_hash_cannot_hide_invalid_event_or_result_semantics(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
    section: str,
    field: str,
    value: Any,
    message: str,
) -> None:
    store, journal = retained_run
    path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(path.read_text())
    entry = manifest[section] if section == "terminal_result" else manifest[section][0]
    payload_path = store.root / entry["key"].removeprefix("evidence://")
    payload = json.loads(payload_path.read_text())
    payload[field] = value
    content = json.dumps(payload).encode()
    payload_path.write_bytes(content)
    entry.update(
        size_bytes=len(content), content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}"
    )
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvidenceIntegrityError, match=message):
        verify_run_manifest(store, journal.evidence_manifest_key)


def test_incomplete_manifest_is_allowed_only_when_explicitly_requested(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
) -> None:
    store, journal = retained_run
    path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(path.read_text())
    manifest["terminal_result"] = None
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvidenceIntegrityError, match="no terminal result"):
        verify_run_manifest(store, journal.evidence_manifest_key)
    assert not verify_run_manifest(
        store, journal.evidence_manifest_key, require_terminal=False
    ).terminal_result_verified


def test_manifest_requires_an_aware_generation_timestamp(
    retained_run: tuple[LocalEvidenceStore, InMemoryRunJournal],
) -> None:
    store, journal = retained_run
    path = store.root / journal.evidence_manifest_key.removeprefix("evidence://")
    manifest = json.loads(path.read_text())
    manifest["generated_at"] = "2026-09-10T12:00:00"
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvidenceIntegrityError, match="manifest violates"):
        verify_run_manifest(store, journal.evidence_manifest_key)
