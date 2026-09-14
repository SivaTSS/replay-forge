import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from replayforge.evidence.diagnostics import ExecutionDiagnostic, bounded_count, diagnostic_archive
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.shared.clock import FrozenClock

RUN = "run_" + "a" * 32


def test_snapshot_is_closed_bounded_and_contains_no_adapter_prose() -> None:
    diagnostic = ExecutionDiagnostic(
        code="private.person@example.invalid",
        action_type="secret action",
        expected_condition_kind="customer label",
        observed_count=2,
    )
    archive = diagnostic_archive(RUN, diagnostic.model_dump(), StructuredRedactor())
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.namelist() == ["diagnostic.json"]
        raw = zipped.read("diagnostic.json")
        payload = json.loads(raw)
    assert len(raw) <= 64 * 1024
    assert payload["observed_count"] == 2
    assert payload["code"] == "other"
    assert payload["action_type"] == "unknown"
    assert b"private.person" not in raw and b"secret action" not in raw
    with pytest.raises(ValidationError):
        ExecutionDiagnostic.model_validate({"code": "target_absent", "ocr_text": "PII"})
    with pytest.raises(ValidationError):
        ExecutionDiagnostic(code="target_absent", observed_count=True)
    assert bounded_count(True) is None
    assert bounded_count(1_000_001) is None
    assert bounded_count(2) == 2


def test_configured_secrets_are_scanned_before_archiving() -> None:
    with pytest.raises(EvidenceRejectedError):
        diagnostic_archive(
            RUN,
            {"code": "target_absent"},
            StructuredRedactor(configured_secrets=("target_absent",)),
        )


def test_finalized_journal_exports_latest_diagnostic_once(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 14, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path, clock)
    journal = InMemoryRunJournal(RUN, clock, evidence_store=store)
    for count in (0, 2):
        journal.record(
            "execution_diagnostic",
            RUN,
            details=ExecutionDiagnostic(
                code="target_ambiguous",
                observed_count=count,
                expected_count=1,
                phase="before_dispatch",
                dispatch_state="not_attempted",
            ).safe_payload(),
        )
    key = journal.finalize({"run_id": RUN, "status": "failure", "code": "target_ambiguous"})
    assert verify_run_manifest(store, key).attachment_count == 1
    manifest = json.loads(store.read(key))
    with zipfile.ZipFile(io.BytesIO(store.read(manifest["attachments"][0]["key"]))) as archive:
        snapshot = json.loads(archive.read("diagnostic.json"))
    assert snapshot["observed_count"] == 2
    assert snapshot["phase"] == "before_dispatch"
    assert (
        json.loads(store.read(manifest["terminal_result"]["key"]))["diagnostic_trace"] == "captured"
    )
    with pytest.raises(RuntimeError, match="already"):
        journal.finalize({"run_id": RUN, "status": "failure"})


def test_unavailable_diagnostic_does_not_change_terminal_result(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 9, 14, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path, clock)
    journal = InMemoryRunJournal(RUN, clock, evidence_store=store)
    journal.record("execution_diagnostic", RUN, details={"observed_count": 2})
    key = journal.finalize({"run_id": RUN, "status": "success"})
    manifest = json.loads(store.read(key))
    result = json.loads(store.read(manifest["terminal_result"]["key"]))
    assert result["status"] == "success"
    assert result["diagnostic_trace"] == "unavailable"
