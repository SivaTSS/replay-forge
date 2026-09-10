"""Safe parsing, canonicalization, hashing, and schema generation for artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import yaml

from replayforge.capabilities.models import CapabilityArtifact


class ArtifactParseError(ValueError):
    """Raised when artifact YAML is unsafe or not a mapping."""


def load_artifact_yaml(content: str) -> CapabilityArtifact:
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ArtifactParseError("artifact is not valid safe YAML") from exc
    if not isinstance(raw, dict):
        raise ArtifactParseError("artifact root must be a mapping")
    return CapabilityArtifact.model_validate(raw)


def dump_artifact_yaml(artifact: CapabilityArtifact) -> str:
    payload = artifact.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def canonical_artifact_json(artifact: CapabilityArtifact) -> bytes:
    payload = artifact.model_dump(mode="json", exclude_none=True)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("artifact_content_hash", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def artifact_content_hash(artifact: CapabilityArtifact) -> str:
    digest = hashlib.sha256(canonical_artifact_json(artifact)).hexdigest()
    return f"sha256:{digest}"


def artifact_json_schema() -> dict[str, Any]:
    return CapabilityArtifact.model_json_schema(mode="validation")
