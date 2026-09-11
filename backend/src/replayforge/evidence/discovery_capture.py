"""Secure capture boundary for a genuine model-driven discovery run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from replayforge.capabilities import (
    CapabilityArtifact,
    artifact_content_hash,
    dump_artifact_yaml,
)


def invoke(base_url: str, timeout_seconds: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "goal": "Look up the synthetic member and return the current savings balance.",
            "application_family": "northstar_member_service",
            "tenant": "harbor",
            "entry_point": "member_search",
            "inputs": {"member_id": "12345"},
            "max_steps": 20,
            "timeout_seconds": timeout_seconds,
        },
        separators=(",", ":"),
    ).encode()
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/discoveries",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds + 30) as response:
            parsed = json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"discovery API returned HTTP {exc.code}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("ReplayForge returned a non-object discovery result")
    return parsed


def validate_result(result: dict[str, Any]) -> CapabilityArtifact:
    if result.get("status") != "success":
        raise RuntimeError("model-driven discovery did not return success")
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("run_"):
        raise RuntimeError("discovery result omitted a valid run ID")
    manifest = result.get("evidence_manifest")
    if not isinstance(manifest, str) or not manifest.startswith(f"evidence://{run_id}/"):
        raise RuntimeError("discovery result omitted a run-bound evidence manifest")

    artifact_payload = result.get("artifact")
    if not isinstance(artifact_payload, dict):
        raise RuntimeError("successful discovery omitted its compiled artifact")
    artifact = CapabilityArtifact.model_validate(artifact_payload)
    provenance = artifact.provenance
    if provenance.discovery_run_id != run_id:
        raise RuntimeError("artifact provenance does not identify the discovery run")
    if provenance.provider != "openai":
        raise RuntimeError("artifact provenance does not identify the OpenAI provider")
    if not provenance.model.strip() or provenance.model == "not-applicable":
        raise RuntimeError("artifact provenance omitted the OpenAI model ID")
    if not provenance.evidence_manifest_key.startswith(f"evidence://{run_id}/"):
        raise RuntimeError("artifact provenance evidence belongs to a different run")

    calculated_hash = artifact_content_hash(artifact)
    if provenance.artifact_content_hash != calculated_hash:
        raise RuntimeError("compiled artifact failed canonical content-hash verification")
    return artifact


def write_new_artifact(path: Path, artifact: CapabilityArtifact) -> None:
    if not path.parent.is_dir():
        raise RuntimeError(f"artifact output directory does not exist: {path.parent}")
    content = dump_artifact_yaml(artifact).encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite existing artifact: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def capture(base_url: str, timeout_seconds: int, artifact_output: Path) -> dict[str, str]:
    result = invoke(base_url, timeout_seconds)
    artifact = validate_result(result)
    write_new_artifact(artifact_output, artifact)
    return {
        "artifact_content_hash": artifact.provenance.artifact_content_hash or "",
        "artifact_output": str(artifact_output),
        "artifact_provenance_manifest": artifact.provenance.evidence_manifest_key,
        "capability_id": artifact.capability.id,
        "evidence_manifest": str(result["evidence_manifest"]),
        "model": artifact.provenance.model,
        "run_id": str(result["run_id"]),
        "status": str(result["status"]),
        "version": artifact.capability.version,
    }
