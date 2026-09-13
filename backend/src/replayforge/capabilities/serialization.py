"""Safe parsing, canonicalization, hashing, and schema generation for artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
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
    payload = _canonicalize(artifact.model_dump(mode="python", exclude_none=True))
    _remove_hash_neutral_schema_defaults(payload)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("artifact_content_hash", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _remove_hash_neutral_schema_defaults(payload: dict[str, Any]) -> None:
    """Preserve hashes when additive schema fields are absent from older artifacts."""

    if not payload.get("failures"):
        payload.pop("failures", None)
    compatibility = payload.get("compatibility")
    if isinstance(compatibility, dict) and compatibility.get("rendered_surface") is False:
        compatibility.pop("rendered_surface", None)
    policy = payload.get("policy")
    if isinstance(policy, dict) and not policy.get("allowed_route_patterns"):
        policy.pop("allowed_route_patterns", None)
    steps = list(payload.get("steps", ()))
    for recovery in payload.get("recoveries", ()):
        if isinstance(recovery, dict):
            steps.extend(recovery.get("steps", ()))
    for step in steps:
        if isinstance(step, dict) and not step.get("failure_refs"):
            step.pop("failure_refs", None)
    _remove_empty_visual_candidates(payload)


def _remove_empty_visual_candidates(value: Any) -> None:
    if isinstance(value, dict):
        if not value.get("visual_candidates"):
            value.pop("visual_candidates", None)
        for item in value.values():
            _remove_empty_visual_candidates(item)
    elif isinstance(value, list):
        for item in value:
            _remove_empty_visual_candidates(item)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, set | frozenset):
        normalized = [_canonicalize(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    return value


def artifact_content_hash(artifact: CapabilityArtifact) -> str:
    digest = hashlib.sha256(canonical_artifact_json(artifact)).hexdigest()
    return f"sha256:{digest}"


def artifact_json_schema() -> dict[str, Any]:
    return CapabilityArtifact.model_json_schema(mode="validation")
