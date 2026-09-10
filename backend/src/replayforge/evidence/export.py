"""Export verified opaque evidence into a stable reviewer scenario bundle."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.evidence.integrity import RunEvidenceManifest, verify_run_manifest
from replayforge.evidence.ports import EvidenceStore
from replayforge.evidence.redaction import StructuredRedactor

_SCENARIO_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True, slots=True)
class EvidenceExportRequest:
    scenario: str
    artifact: CapabilityArtifact
    source_manifest_key: str
    commands: tuple[str, ...]
    commit_sha: str

    def __post_init__(self) -> None:
        validations = (
            (_SCENARIO_PATTERN, self.scenario, "scenario"),
            (_COMMIT_PATTERN, self.commit_sha, "commit SHA"),
        )
        for pattern, value, label in validations:
            if pattern.fullmatch(value) is None:
                raise ValueError(f"invalid {label}")
        if (
            not self.commands
            or len(self.commands) > 20
            or any(not command.strip() or len(command) > 2_000 for command in self.commands)
        ):
            raise ValueError("one to twenty bounded reproduction commands are required")


@dataclass(frozen=True, slots=True)
class EvidenceExport:
    destination: Path
    run_id: str
    manifest_hash: str


def export_evidence_bundle(
    store: EvidenceStore,
    destination: Path,
    request: EvidenceExportRequest,
    *,
    configured_secrets: tuple[str, ...] = (),
) -> EvidenceExport:
    """Verify source evidence and atomically publish a reviewer-readable bundle."""

    if destination.exists():
        raise FileExistsError("evidence export destination already exists")
    if destination.name != request.scenario:
        raise ValueError("evidence destination name must match the scenario")
    verification = verify_run_manifest(store, request.source_manifest_key)
    manifest_content = store.read(request.source_manifest_key)
    source = RunEvidenceManifest.model_validate_json(manifest_content)
    if source.terminal_result is None:
        raise RuntimeError("verified completed manifest must contain a terminal result")

    events_content = b"".join(
        store.read(entry.key).rstrip(b"\n") + b"\n" for entry in source.events
    )
    result_content = store.read(source.terminal_result.key).rstrip(b"\n") + b"\n"
    files = {
        "events.jsonl": _file_metadata(events_content),
        "result.json": _file_metadata(result_content),
    }
    redaction_directives = sorted(
        {
            directive
            for entry in (*source.events, source.terminal_result)
            for directive in entry.redaction_directives
        }
    )
    bundle_manifest = {
        "artifact": {
            "capability_id": request.artifact.capability.id,
            "content_hash": artifact_content_hash(request.artifact),
            "version": request.artifact.capability.version,
        },
        "commands": list(request.commands),
        "commit_sha": request.commit_sha,
        "files": files,
        "generated_at": source.generated_at.isoformat().replace("+00:00", "Z"),
        "redaction": {
            "directives": redaction_directives,
            "source_manifest_verified": True,
        },
        "run_id": source.run_id,
        "scenario": request.scenario,
        "schema_version": "evidence-bundle.v1",
        "source_manifest": {
            "content_hash": verification.manifest_hash,
            "key": request.source_manifest_key,
        },
    }
    sanitized_manifest = StructuredRedactor(configured_secrets=configured_secrets).sanitize_json(
        bundle_manifest, {}, run_salt=source.run_id
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        _durable_write(temporary / "events.jsonl", events_content)
        _durable_write(temporary / "result.json", result_content)
        _durable_write(temporary / "manifest.json", sanitized_manifest.content + b"\n")
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return EvidenceExport(destination, source.run_id, verification.manifest_hash)


def _file_metadata(content: bytes) -> dict[str, object]:
    return {
        "content_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "size_bytes": len(content),
    }


def _durable_write(destination: Path, content: bytes) -> None:
    with destination.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
