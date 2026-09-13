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
from replayforge.evidence.models import RetentionClass, SanitizedEvidence
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
    artifact_content = (destination / "artifact.yaml").read_bytes()
    events = (destination / "events.jsonl").read_bytes()
    result = (destination / "result.json").read_bytes()
    screenshot = (destination / "screenshots/001.png").read_bytes()
    trace = (destination / "trace.zip").read_bytes()
    assert manifest["run_id"] == journal.run_id
    assert manifest["artifact"]["file"] == "artifact.yaml"
    assert manifest["files"]["artifact.yaml"] == {
        "content_hash": f"sha256:{hashlib.sha256(artifact_content).hexdigest()}",
        "size_bytes": len(artifact_content),
    }
    assert load_artifact_yaml(artifact_content.decode()) == _request(journal).artifact
    assert manifest["redaction"]["source_manifest_verified"] is True
    assert manifest["redaction"]["directives"] == [
        "mask:input",
        "redact:outputs.balance",
        "sanitize:trace",
    ]
    assert manifest["attachments"] == {
        "screenshots/001.png": {
            "content_hash": f"sha256:{hashlib.sha256(screenshot).hexdigest()}",
            "media_type": "image/png",
            "size_bytes": len(screenshot),
        },
        "trace.zip": {
            "content_hash": f"sha256:{hashlib.sha256(trace).hexdigest()}",
            "media_type": "application/zip",
            "size_bytes": len(trace),
        },
    }
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


def test_bundle_verifier_rejects_undeclared_file(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    (destination / "unreviewed.txt").write_text("extra")

    with pytest.raises(EvidenceBundleIntegrityError, match="undeclared"):
        verify_evidence_bundle(destination)


def test_bundle_verifier_rejects_symbolic_links(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    (destination / "link").symlink_to(destination / "result.json")

    with pytest.raises(EvidenceBundleIntegrityError, match="symbolic"):
        verify_evidence_bundle(destination)


def test_bundle_verifier_rejects_artifact_metadata_mismatch(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"]["version"] = "9.9.9"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceBundleIntegrityError, match="does not match"):
        verify_evidence_bundle(destination)


def test_bundle_verifier_accepts_legacy_bundle_without_embedded_artifact(
    tmp_path: Path,
) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"].pop("file")
    manifest["files"].pop("artifact.yaml")
    (destination / "artifact.yaml").unlink()
    manifest_path.write_text(json.dumps(manifest))

    assert verify_evidence_bundle(destination).run_id == journal.run_id


@pytest.mark.parametrize("attachment", ["screenshots/001.png", "trace.zip"])
def test_bundle_verifier_rejects_missing_attachment(tmp_path: Path, attachment: str) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    (destination / attachment).unlink()

    with pytest.raises(EvidenceBundleIntegrityError, match="attachment is missing"):
        verify_evidence_bundle(destination)


@pytest.mark.parametrize(
    ("attachment", "invalid_content", "message"),
    [
        ("screenshots/001.png", b"not-a-png", "screenshot.*invalid signature"),
        ("trace.zip", b"not-a-zip", "trace.*invalid signature"),
    ],
)
def test_bundle_verifier_rejects_invalid_attachment_signature(
    tmp_path: Path,
    attachment: str,
    invalid_content: bytes,
    message: str,
) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    attachment_path = destination / attachment
    attachment_path.write_bytes(invalid_content)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["attachments"][attachment].update(
        content_hash=f"sha256:{hashlib.sha256(invalid_content).hexdigest()}",
        size_bytes=len(invalid_content),
    )
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceBundleIntegrityError, match=message):
        verify_evidence_bundle(destination)


@pytest.mark.parametrize("mutation", ["unsafe_path", "duplicate_trace"])
def test_bundle_verifier_rejects_invalid_attachment_manifest(tmp_path: Path, mutation: str) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "unsafe_path":
        manifest["attachments"]["../escape.png"] = manifest["attachments"].pop(
            "screenshots/001.png"
        )
    else:
        manifest["attachments"]["screenshots/999.png"] = {**manifest["attachments"]["trace.zip"]}
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceBundleIntegrityError, match="manifest is invalid"):
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


def test_export_rejects_secret_in_embedded_artifact(tmp_path: Path) -> None:
    store, journal = _retained_run(tmp_path)
    request = _request(journal)
    unsafe_artifact = request.artifact.model_copy(
        update={
            "capability": request.artifact.capability.model_copy(
                update={"description": "known-sensitive-value"}
            )
        }
    )

    with pytest.raises(EvidenceRejectedError):
        export_evidence_bundle(
            store,
            tmp_path / "replay-success",
            replace(request, artifact=unsafe_artifact),
            configured_secrets=("known-sensitive-value",),
        )

    assert not (tmp_path / "replay-success").exists()


def test_bundle_verifier_rejects_secret_even_when_manifest_hash_is_rewritten(
    tmp_path: Path,
) -> None:
    store, journal = _retained_run(tmp_path)
    destination = tmp_path / "replay-success"
    export_evidence_bundle(store, destination, _request(journal))
    events_path = destination / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events[0]["details"]["note"] = "sk-abcdefghijklmnopqrst"
    content = b"".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for event in events
    )
    events_path.write_bytes(content)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["events.jsonl"] = {
        "content_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "size_bytes": len(content),
    }
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(EvidenceBundleIntegrityError, match="unsafe text"):
        verify_evidence_bundle(destination)


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
