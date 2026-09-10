from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.models import EvidenceRecord, RetentionClass, SanitizedEvidence
from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.policy.types import DataClassification
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


def journal(redactor: StructuredRedactor | None = None) -> InMemoryRunJournal:
    return InMemoryRunJournal(
        str(new_id(EntityKind.RUN)),
        FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC)),
        redactor or StructuredRedactor(),
    )


def test_journal_orders_events_and_exposes_opaque_manifest() -> None:
    recorder = journal()

    recorder.record("replay_started", recorder.run_id)
    recorder.record("action_result", recorder.run_id, step_id="search.submit")

    assert [event.sequence for event in recorder.events()] == [1, 2]
    assert recorder.events()[1].step_id == "search.submit"
    assert recorder.evidence_manifest_key == f"evidence://{recorder.run_id}/manifest.json"


def test_journal_drops_forbidden_detail_keys() -> None:
    recorder = journal()

    recorder.record(
        "policy_evaluated",
        recorder.run_id,
        details={"decision": "allow", "authorization_token": "must-not-survive"},
    )

    assert recorder.events()[0].details == {"decision": "allow"}


def test_journal_rejects_configured_secret_value() -> None:
    recorder = journal(StructuredRedactor(configured_secrets=("highly-sensitive",)))

    with pytest.raises(EvidenceRejectedError):
        recorder.record("unsafe_event", recorder.run_id, details={"note": "highly-sensitive"})


@pytest.mark.parametrize(
    ("event_type", "step_id"),
    [("Invalid Event", None), ("valid_event", "invalid step")],
)
def test_journal_validates_event_identity(event_type: str, step_id: str | None) -> None:
    recorder = journal()

    with pytest.raises(ValueError):
        recorder.record(event_type, recorder.run_id, step_id=step_id)


def test_journal_rejects_cross_run_event() -> None:
    recorder = journal()

    with pytest.raises(ValueError, match="different run"):
        recorder.record("replay_started", str(new_id(EntityKind.RUN)))


def test_journal_persists_redacted_events_and_latest_manifest(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "evidence", clock)
    recorder = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)

    recorder.record(
        "policy_evaluated",
        recorder.run_id,
        details={"decision": "allow", "authorization_token": "must-not-survive"},
    )
    recorder.record("replay_failed", recorder.run_id, details={"code": "target_missing"})

    manifest = json.loads(store.read(recorder.evidence_manifest_key))
    assert manifest["schema_version"] == "1.0"
    assert manifest["run_id"] == recorder.run_id
    assert [entry["retention_class"] for entry in manifest["events"]] == [
        "operational",
        "failure",
    ]
    assert manifest["events"][0]["redaction_directives"] == ["drop:authorization_token"]
    assert [entry["key"] for entry in manifest["events"]] != [recorder.evidence_manifest_key]
    for sequence, entry in enumerate(manifest["events"], start=1):
        content = store.read(entry["key"])
        event = json.loads(content)
        assert event["sequence"] == sequence
        assert entry["content_hash"] == f"sha256:{hashlib.sha256(content).hexdigest()}"
        assert "must-not-survive" not in content.decode()


def test_journal_retains_human_input_as_human_audit_evidence(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "evidence", clock)
    recorder = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)

    recorder.record(
        "human_input_applied",
        recorder.run_id,
        details={"input_type": "text", "character_count": 5},
    )

    manifest = json.loads(store.read(recorder.evidence_manifest_key))
    assert manifest["events"][0]["retention_class"] == "human_audit"


def test_journal_persists_sanitized_binary_attachment(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "evidence", clock)
    recorder = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)
    recorder.record("intervention_required", recorder.run_id)
    screenshot = SanitizedEvidence(
        b"\x89PNG\r\n\x1a\nmasked-synthetic-frame",
        "image/png",
        ("mask:input",),
    )

    attachment = recorder.attach_sanitized(
        "intervention-before",
        screenshot,
        RetentionClass.HUMAN_AUDIT,
    )
    recorder.finalize({"status": "failure", "run_id": recorder.run_id})

    manifest = json.loads(store.read(recorder.evidence_manifest_key))
    assert manifest["attachments"] == [
        {
            "content_hash": attachment.content_hash,
            "created_at": "2026-09-10T12:00:00Z",
            "evidence_id": str(attachment.id),
            "key": attachment.key,
            "media_type": "image/png",
            "redaction_directives": ["mask:input"],
            "retention_class": "human_audit",
            "size_bytes": len(screenshot.content),
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        SanitizedEvidence(b"", "image/png", ()),
        SanitizedEvidence(b"{}", "application/json", ()),
        SanitizedEvidence(b"not-a-png", "image/png", ()),
        SanitizedEvidence(b"not-a-zip", "application/zip", ()),
    ],
)
def test_journal_rejects_invalid_attachment(payload: SanitizedEvidence) -> None:
    recorder = journal()

    with pytest.raises((ValueError, RuntimeError)):
        recorder.attach_sanitized("invalid", payload, RetentionClass.OPERATIONAL)


def test_journal_requires_store_for_valid_attachment() -> None:
    recorder = journal()

    with pytest.raises(RuntimeError, match="require an evidence store"):
        recorder.attach_sanitized(
            "screenshot",
            SanitizedEvidence(b"\x89PNG\r\n\x1a\nmasked", "image/png", ("mask:all",)),
            RetentionClass.OPERATIONAL,
        )


class FailingEvidenceStore:
    def write(
        self,
        run_id: str,
        kind: str,
        payload: SanitizedEvidence,
        retention_class: RetentionClass,
    ) -> EvidenceRecord:
        del run_id, kind, payload, retention_class
        raise OSError("evidence volume unavailable")

    def read(self, _key: str) -> bytes:
        raise AssertionError("not used")


def test_journal_does_not_retain_event_when_evidence_write_fails() -> None:
    recorder = journal()
    recorder.evidence_store = FailingEvidenceStore()

    with pytest.raises(OSError, match="unavailable"):
        recorder.record("replay_started", recorder.run_id)

    assert recorder.events() == ()


def test_journal_finalizes_once_and_redacts_terminal_result(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "evidence", clock)
    recorder = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)
    recorder.record("checkpoint_verified", recorder.run_id)

    manifest_key = recorder.finalize(
        {
            "status": "success",
            "run_id": recorder.run_id,
            "outputs": {"member_id": "12345", "balance": "1420.57"},
        },
        {
            "outputs.member_id": DataClassification.CUSTOMER_IDENTIFIER,
            "outputs.balance": DataClassification.FINANCIAL,
        },
    )

    manifest = json.loads(store.read(manifest_key))
    terminal = manifest["terminal_result"]
    result = json.loads(store.read(terminal["key"]))
    assert result["outputs"]["member_id"].startswith("customer_")
    assert result["outputs"]["balance"] == "[REDACTED_FINANCIAL]"
    assert terminal["redaction_directives"] == [
        "redact:outputs.balance",
        "tokenize:outputs.member_id",
    ]

    with pytest.raises(RuntimeError, match="already been finalized"):
        recorder.finalize({"status": "success", "run_id": recorder.run_id})
    with pytest.raises(RuntimeError, match="after run finalization"):
        recorder.record("action_result", recorder.run_id)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "success", "run_id": "wrong"},
        {"status": "intervention_required"},
    ],
)
def test_journal_rejects_invalid_terminal_result(result: dict[str, object]) -> None:
    recorder = journal()
    if "run_id" not in result:
        result["run_id"] = recorder.run_id

    with pytest.raises(ValueError):
        recorder.finalize(result)
