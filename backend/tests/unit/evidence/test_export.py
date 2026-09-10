import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.evidence.export import (
    EvidenceBundleIntegrityError,
    EvidenceExportRequest,
    export_evidence_bundle,
    verify_evidence_bundle,
)
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.redaction import EvidenceRejectedError
from replayforge.policy.types import DataClassification
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


def _retained_run(tmp_path: Path) -> tuple[LocalEvidenceStore, InMemoryRunJournal]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    store = LocalEvidenceStore(tmp_path / "runtime", clock)
    journal = InMemoryRunJournal(str(new_id(EntityKind.RUN)), clock, evidence_store=store)
    journal.record("replay_started", journal.run_id)
    journal.finalize(
        {"status": "success", "run_id": journal.run_id, "outputs": {"balance": "42.00"}},
        {"outputs.balance": DataClassification.FINANCIAL},
    )
    return store, journal


def _request(journal: InMemoryRunJournal) -> EvidenceExportRequest:
    artifact_path = (
        Path(__file__).resolve().parents[4]
        / "capabilities"
        / "member.lookup_savings_balance"
        / "1.0.0.yaml"
    )
    return EvidenceExportRequest(
        scenario="replay-success",
        artifact=load_artifact_yaml(artifact_path.read_text()),
        source_manifest_key=journal.evidence_manifest_key,
        commands=("curl http://127.0.0.1:8000/api/v1/capabilities/example/invoke",),
        commit_sha="abcdef1",
    )


def test_export_writes_stable_verified_bundle(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"

    exported = export_evidence_bundle(store, destination, _request(journal))

    assert exported.destination == destination
    manifest = json.loads((destination / "manifest.json").read_text())
    events = (destination / "events.jsonl").read_bytes()
    result = (destination / "result.json").read_bytes()
    assert manifest["run_id"] == journal.run_id
    assert manifest["redaction"]["source_manifest_verified"] is True
    assert manifest["redaction"]["directives"] == ["redact:outputs.balance"]
    assert manifest["files"]["events.jsonl"]["content_hash"] == (
        f"sha256:{hashlib.sha256(events).hexdigest()}"
    )
    assert manifest["files"]["result.json"]["size_bytes"] == len(result)
    assert json.loads(result)["outputs"]["balance"] == "[REDACTED_FINANCIAL]"
    assert verify_evidence_bundle(destination).run_id == journal.run_id


def test_bundle_verifier_rejects_tampering(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    (destination / "result.json").write_text("{}")

    with pytest.raises(EvidenceBundleIntegrityError, match="hash or size"):
        verify_evidence_bundle(destination)


def test_bundle_verifier_rejects_missing_file(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    (destination / "events.jsonl").unlink()

    with pytest.raises(EvidenceBundleIntegrityError, match="missing"):
        verify_evidence_bundle(destination)


@pytest.mark.parametrize("mutation", ["scenario", "event_sequence", "result_run"])
def test_bundle_verifier_rejects_semantic_inconsistency(tmp_path: Path, mutation: str) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "scenario":
        manifest["scenario"] = "different-scenario"
    elif mutation == "event_sequence":
        events_path = destination / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        events[0]["sequence"] = 9
        content = b"".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for event in events
        )
        events_path.write_bytes(content)
        manifest["files"]["events.jsonl"] = {
            "content_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "size_bytes": len(content),
        }
    else:
        result_path = destination / "result.json"
        result = json.loads(result_path.read_text())
        result["run_id"] = str(new_id(EntityKind.RUN))
        content = json.dumps(result, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        result_path.write_bytes(content)
        manifest["files"]["result.json"] = {
            "content_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "size_bytes": len(content),
        }
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceBundleIntegrityError):
        verify_evidence_bundle(destination)


def test_export_refuses_to_replace_existing_evidence(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    destination.mkdir()

    with pytest.raises(FileExistsError):
        export_evidence_bundle(store, destination, _request(journal))


def test_export_rejects_secret_in_reproduction_command(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    request = replace(_request(journal), commands=("run --key highly-sensitive-value",))

    with pytest.raises(EvidenceRejectedError):
        export_evidence_bundle(
            store,
            tmp_path / "replay-success",
            request,
            configured_secrets=("highly-sensitive-value",),
        )

    assert not (tmp_path / "replay-success").exists()


def test_export_request_rejects_invalid_metadata(tmp_path: Path) -> None:
    _, journal = _retained_run(tmp_path)
    valid = _request(journal)

    with pytest.raises(ValueError):
        EvidenceExportRequest(
            "../escape",
            valid.artifact,
            valid.source_manifest_key,
            valid.commands,
            valid.commit_sha,
        )
    with pytest.raises(ValueError):
        EvidenceExportRequest(
            valid.scenario,
            valid.artifact,
            valid.source_manifest_key,
            (),
            valid.commit_sha,
        )
    with pytest.raises(ValueError):
        EvidenceExportRequest(
            valid.scenario,
            valid.artifact,
            valid.source_manifest_key,
            valid.commands,
            "main",
        )
