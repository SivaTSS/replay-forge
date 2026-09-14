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

_COMMITTED_ARTIFACT_HASHES = {
    "member.transaction_investigation/1.0.1.yaml": (
        "sha256:354824ebbd3ba6b77a126537b3c1d442e09a62235d275b9077ef1805024e7a03"
    ),
    "member.transaction_investigation/1.0.2.yaml": (
        "sha256:a5b82adb1f1f12dcf140cb6c057f0aa7aecbe340480bd3e7153698c2a8f403fb"
    ),
    "member.servicing_loan_payoff_quote/1.0.1.yaml": (
        "sha256:ea95e6c1a0aa65528270e7078374eb8997f57a573389fa3a1d827bc516d8f737"
    ),
    "member.servicing_loan_payoff_quote/1.0.2.yaml": (
        "sha256:8ae472cd3a86db94cae8c212711a4433c1eb7cfa217c5e995b3bbe6b9ef6c3e5"
    ),
    "member.temporary_card_lock/1.0.1.yaml": (
        "sha256:b403f5b4de5efb5dcad4c3b0dc67c670f496f3d9f798324c79e9e9bcce3b5de9"
    ),
    "member.temporary_card_lock/1.0.2.yaml": (
        "sha256:fe1d31f2f48f4f20cdf4eb18e1af21ca4b2a892cb431fe23f3ead937b796dd16"
    ),
}


def test_yaml_cannot_silently_overwrite_reviewed_fields() -> None:
    with pytest.raises(ArtifactParseError, match="valid safe YAML"):
        load_artifact_yaml("schema_version: '1.4'\nschema_version: '1.0'\n")


def test_yaml_round_trip_preserves_artifact_and_hash(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    restored = load_artifact_yaml(dump_artifact_yaml(artifact))

    assert restored == artifact
    assert artifact_content_hash(restored) == artifact_content_hash(artifact)
    assert artifact_content_hash(artifact).startswith("sha256:")


def test_every_committed_artifact_retains_its_contract_and_canonical_hash() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    artifact_root = repository_root / "capabilities"
    paths = tuple(sorted(artifact_root.rglob("*.yaml")))

    assert {path.relative_to(artifact_root).as_posix() for path in paths} == set(
        _COMMITTED_ARTIFACT_HASHES
    )
    for path in paths:
        relative_path = path.relative_to(artifact_root).as_posix()
        artifact = load_artifact_yaml(path.read_text())
        digest = artifact_content_hash(artifact)

        assert digest == _COMMITTED_ARTIFACT_HASHES[relative_path]
        assert artifact.provenance.artifact_content_hash == digest
        assert load_artifact_yaml(dump_artifact_yaml(artifact)) == artifact
        assert CapabilityArtifact.model_validate(artifact.model_dump(mode="python")) == artifact


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
    assert schema["properties"]["schema_version"]["const"] == "1.4"


def test_committed_schema_matches_generated_contract() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (repository_root / "schemas" / "capability-artifact-v1.schema.json").read_text()
    )

    assert committed == artifact_json_schema()
