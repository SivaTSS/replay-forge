"""Export verified opaque evidence into a stable reviewer scenario bundle."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import (
    artifact_content_hash,
    dump_artifact_yaml,
    load_artifact_yaml,
)
from replayforge.evidence.integrity import (
    TerminalResultEvidence,
    verify_run_manifest,
)
from replayforge.evidence.models import EventEvidence, RunEvidenceManifest
from replayforge.evidence.ports import EvidenceStore
from replayforge.evidence.redaction import StructuredRedactor

_SCENARIO_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
_ATTACHMENT_PATH_PATTERN = re.compile(r"^(?:screenshots/[0-9]{3}\.png|trace\.zip)$")


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


class EvidenceBundleIntegrityError(ValueError):
    """Raised when an exported reviewer bundle is incomplete or inconsistent."""


class _BundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BundleFile(_BundleModel):
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=20_000_000)


class BundleAttachment(BundleFile):
    media_type: Literal["image/png", "application/zip"]


class BundleArtifact(_BundleModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    file: Literal["artifact.yaml"] | None = None
    version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")


class BundleRedaction(_BundleModel):
    directives: tuple[str, ...]
    source_manifest_verified: Literal[True]


class BundleSourceManifest(_BundleModel):
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    key: str = Field(pattern=r"^evidence://.+$")


class EvidenceBundleManifest(_BundleModel):
    artifact: BundleArtifact
    attachments: dict[str, BundleAttachment] = Field(default_factory=dict, max_length=100)
    commands: tuple[str, ...] = Field(min_length=1, max_length=20)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    files: dict[Literal["artifact.yaml", "events.jsonl", "result.json"], BundleFile]
    generated_at: datetime
    redaction: BundleRedaction
    run_id: str
    scenario: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    schema_version: Literal["evidence-bundle.v1"]
    source_manifest: BundleSourceManifest

    @field_validator("files")
    @classmethod
    def require_all_files(
        cls,
        value: dict[Literal["artifact.yaml", "events.jsonl", "result.json"], BundleFile],
    ) -> dict[Literal["artifact.yaml", "events.jsonl", "result.json"], BundleFile]:
        if not {"events.jsonl", "result.json"}.issubset(value):
            raise ValueError("bundle must declare all required files")
        return value

    @model_validator(mode="after")
    def require_consistent_artifact_file(self) -> EvidenceBundleManifest:
        if (self.artifact.file is None) != ("artifact.yaml" not in self.files):
            raise ValueError("bundle artifact file declaration is inconsistent")
        return self

    @field_validator("attachments")
    @classmethod
    def require_safe_attachment_paths(
        cls, value: dict[str, BundleAttachment]
    ) -> dict[str, BundleAttachment]:
        if any(_ATTACHMENT_PATH_PATTERN.fullmatch(path) is None for path in value):
            raise ValueError("bundle attachment path is invalid")
        if sum(item.media_type == "application/zip" for item in value.values()) > 1:
            raise ValueError("bundle may contain at most one trace archive")
        return value


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
    artifact_content = dump_artifact_yaml(request.artifact).encode()
    redactor = StructuredRedactor(configured_secrets=configured_secrets)
    redactor.validate_text(artifact_content.decode("utf-8"))
    files = {
        "artifact.yaml": _file_metadata(artifact_content),
        "events.jsonl": _file_metadata(events_content),
        "result.json": _file_metadata(result_content),
    }
    attachment_content: dict[str, bytes] = {}
    attachment_metadata: dict[str, dict[str, object]] = {}
    screenshot_sequence = 0
    for entry in source.attachments:
        content = store.read(entry.key)
        if entry.media_type == "image/png":
            screenshot_sequence += 1
            relative_path = f"screenshots/{screenshot_sequence:03d}.png"
        elif entry.media_type == "application/zip":
            relative_path = "trace.zip"
            if relative_path in attachment_content:
                raise RuntimeError("source evidence contains more than one trace archive")
        else:
            raise RuntimeError("source evidence contains an unsupported attachment")
        attachment_content[relative_path] = content
        attachment_metadata[relative_path] = {
            **_file_metadata(content),
            "media_type": entry.media_type,
        }
    redaction_directives = sorted(
        {
            directive
            for entry in (*source.events, *source.attachments, source.terminal_result)
            for directive in entry.redaction_directives
        }
    )
    bundle_manifest = {
        "artifact": {
            "capability_id": request.artifact.capability.id,
            "content_hash": artifact_content_hash(request.artifact),
            "file": "artifact.yaml",
            "version": request.artifact.capability.version,
        },
        "attachments": attachment_metadata,
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
    sanitized_manifest = redactor.sanitize_json(bundle_manifest, {}, run_salt=source.run_id)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        _durable_write(temporary / "artifact.yaml", artifact_content)
        _durable_write(temporary / "events.jsonl", events_content)
        _durable_write(temporary / "result.json", result_content)
        for relative_path, content in attachment_content.items():
            attachment_path = temporary / relative_path
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            _durable_write(attachment_path, content)
        _durable_write(temporary / "manifest.json", sanitized_manifest.content + b"\n")
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return EvidenceExport(destination, source.run_id, verification.manifest_hash)


def verify_evidence_bundle(directory: Path) -> EvidenceExport:
    """Verify a stable bundle without requiring its transient source store."""

    try:
        manifest_content = (directory / "manifest.json").read_bytes()
        manifest = EvidenceBundleManifest.model_validate_json(manifest_content)
    except (OSError, ValidationError) as error:
        raise EvidenceBundleIntegrityError("evidence bundle manifest is invalid") from error
    if directory.name != manifest.scenario:
        raise EvidenceBundleIntegrityError("bundle directory does not match its scenario")

    content_by_name: dict[str, bytes] = {}
    for name, declared in manifest.files.items():
        try:
            content = (directory / name).read_bytes()
        except OSError as error:
            raise EvidenceBundleIntegrityError("evidence bundle file is missing") from error
        if _file_metadata(content) != declared.model_dump(mode="python"):
            raise EvidenceBundleIntegrityError("evidence bundle file hash or size does not match")
        content_by_name[name] = content

    if manifest.artifact.file is not None:
        try:
            embedded_artifact = load_artifact_yaml(
                content_by_name[manifest.artifact.file].decode("utf-8")
            )
        except (UnicodeDecodeError, ValidationError, ValueError) as error:
            raise EvidenceBundleIntegrityError("embedded capability artifact is invalid") from error
        if (
            embedded_artifact.capability.id != manifest.artifact.capability_id
            or embedded_artifact.capability.version != manifest.artifact.version
            or artifact_content_hash(embedded_artifact) != manifest.artifact.content_hash
        ):
            raise EvidenceBundleIntegrityError(
                "embedded capability artifact does not match its bundle metadata"
            )

    for relative_path, declared in manifest.attachments.items():
        try:
            content = (directory / relative_path).read_bytes()
        except OSError as error:
            raise EvidenceBundleIntegrityError("evidence bundle attachment is missing") from error
        if _file_metadata(content) != declared.model_dump(mode="python", exclude={"media_type"}):
            raise EvidenceBundleIntegrityError("evidence attachment hash or size does not match")
        if declared.media_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EvidenceBundleIntegrityError("evidence screenshot has an invalid signature")
        if declared.media_type == "application/zip" and not content.startswith(b"PK"):
            raise EvidenceBundleIntegrityError("evidence trace has an invalid signature")

    events = [line for line in content_by_name["events.jsonl"].splitlines() if line]
    if not events:
        raise EvidenceBundleIntegrityError("evidence bundle has no events")
    for sequence, content in enumerate(events, start=1):
        try:
            event = EventEvidence.model_validate_json(content)
        except ValidationError as error:
            raise EvidenceBundleIntegrityError("evidence bundle event is invalid") from error
        if event.run_id != manifest.run_id or event.sequence != sequence:
            raise EvidenceBundleIntegrityError("evidence bundle events are not ordered for the run")
    try:
        result = TerminalResultEvidence.model_validate_json(content_by_name["result.json"])
    except ValidationError as error:
        raise EvidenceBundleIntegrityError("evidence bundle result is invalid") from error
    if result.run_id != manifest.run_id:
        raise EvidenceBundleIntegrityError("evidence bundle result belongs to a different run")

    StructuredRedactor().sanitize_json(
        manifest.model_dump(mode="json"), {}, run_salt=manifest.run_id
    )
    return EvidenceExport(
        directory,
        manifest.run_id,
        f"sha256:{hashlib.sha256(manifest_content).hexdigest()}",
    )


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
