import json
from pathlib import Path
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import (
    ArtifactParseError,
    artifact_content_hash,
    artifact_json_schema,
    canonical_artifact_json,
    dump_artifact_yaml,
    load_artifact_yaml,
)


def test_yaml_round_trip_preserves_artifact_and_hash(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    restored = load_artifact_yaml(dump_artifact_yaml(artifact))

    assert restored == artifact
    assert artifact_content_hash(restored) == artifact_content_hash(artifact)
    assert artifact_content_hash(artifact).startswith("sha256:")


def test_declared_content_hash_is_excluded_from_hash_input(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    digest = artifact_content_hash(artifact)
    valid_artifact_data["provenance"]["artifact_content_hash"] = digest
    artifact_with_digest = CapabilityArtifact.model_validate(valid_artifact_data)

    assert artifact_content_hash(artifact_with_digest) == digest


def test_empty_additive_failure_fields_are_hash_neutral(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    canonical = json.loads(canonical_artifact_json(artifact))

    assert "failures" not in canonical
    assert all("failure_refs" not in step for step in canonical["steps"])


def test_declared_failure_metadata_is_hash_covered(
    valid_artifact_data: dict[str, Any],
) -> None:
    baseline = CapabilityArtifact.model_validate(valid_artifact_data)
    valid_artifact_data["steps"][1]["failure_refs"] = ["permission_denied"]
    valid_artifact_data["failures"] = [
        {
            "code": "permission_denied",
            "description": "The role cannot view the member.",
            "detect": {"kind": "text", "value": "Permission denied"},
            "allowed_after_steps": ["search.submit"],
            "expected_state": "member_results",
            "observed_state": "permission_denied",
        }
    ]
    with_failure = CapabilityArtifact.model_validate(valid_artifact_data)

    assert artifact_content_hash(with_failure) != artifact_content_hash(baseline)
    canonical = json.loads(canonical_artifact_json(with_failure))
    assert canonical["failures"][0]["code"] == "permission_denied"
    assert canonical["steps"][1]["failure_refs"] == ["permission_denied"]


def test_unsafe_yaml_tag_is_rejected() -> None:
    payload = "!!python/object/apply:os.system ['echo unsafe']"

    with pytest.raises(ArtifactParseError, match="safe YAML"):
        load_artifact_yaml(payload)


def test_non_mapping_yaml_is_rejected() -> None:
    with pytest.raises(ArtifactParseError, match="root must be a mapping"):
        load_artifact_yaml("- not\n- an\n- artifact\n")


def test_generated_schema_identifies_artifact_contract() -> None:
    schema = artifact_json_schema()

    assert schema["title"] == "CapabilityArtifact"
    assert schema["properties"]["schema_version"]["enum"] == ["1.0", "1.1", "1.2"]


def test_committed_schema_matches_generated_contract() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (repository_root / "schemas" / "capability-artifact-v1.schema.json").read_text()
    )

    assert committed == artifact_json_schema()
