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
    "member.loan_payoff_quote/1.0.0.yaml": (
        "sha256:a85fd3518f15f3a4d643792d4b4124d7bf622d26d9dd84aa912d3b3340e02d12"
    ),
    "member.lookup_savings_balance/1.0.0.yaml": (
        "sha256:2e4ec4920ed4726195c620b4c7accdfd1642dfd03dc01a2c4bf4e8e30691d208"
    ),
    "member.lookup_savings_balance/1.0.1.yaml": (
        "sha256:06f64e8f546c416b96959c2ebbe03071baeb6d11eb9426baa87c1a39657ff5c0"
    ),
    "member.lookup_savings_balance/1.0.2.yaml": (
        "sha256:f953de8c0de6e8dfb7fac8725ff6e65cdce6eb37b5a9cbf49debdb909207c2e5"
    ),
    "member.lookup_savings_balance/2.0.0.yaml": (
        "sha256:53255c7e15629044f8d54cefd0a13338494c3dde44b7fa81767efba105967353"
    ),
    "member.lookup_savings_balance/3.0.0.yaml": (
        "sha256:5d0d0c4765bcf1b1a8169da5303490421d7032d0a0acb6dbe34f057da9c49411"
    ),
    "member.lookup_savings_balance/3.1.0.yaml": (
        "sha256:e304e76daa6d1410cf496f3a90eaa47913540de9327c517de121fe2292b7dc88"
    ),
    "member.lookup_savings_balance/3.2.0.yaml": (
        "sha256:7bb65d0ade7518edd1ba8b5543e3dc0f649397b6d321a520a0462105b6a162cf"
    ),
    "member.temporary_card_lock/1.0.0.yaml": (
        "sha256:b8844028e13118a6ab091ed9ba460eeee97ff6a3511d1d9d9faabfb5685294a9"
    ),
    "member.transaction_investigation/1.0.0.yaml": (
        "sha256:07ec19a67527a5c4f7e9a24dc1b3aa884990e636b4e97952c0e36645775892d2"
    ),
}


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
    assert schema["properties"]["schema_version"]["enum"] == [
        "1.0",
        "1.1",
        "1.2",
        "1.3",
        "1.4",
    ]


def test_committed_schema_matches_generated_contract() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (repository_root / "schemas" / "capability-artifact-v1.schema.json").read_text()
    )

    assert committed == artifact_json_schema()
