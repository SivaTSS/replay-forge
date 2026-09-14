from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import (
    CapabilityConflictError,
    CapabilityIntegrityError,
    CapabilityNotFoundError,
    CapabilityPublicationError,
    InMemoryCapabilityRegistry,
    LocalCapabilityRegistry,
)
from replayforge.shared.clock import FrozenClock


def _artifact(data: dict[str, Any], version: str = "1.0.0") -> CapabilityArtifact:
    raw = deepcopy(data)
    raw["capability"]["version"] = version
    return CapabilityArtifact.model_validate(raw)


def test_registry_publishes_and_resolves_exact_version(
    valid_artifact_data: dict[str, Any],
) -> None:
    instant = datetime(2026, 9, 10, 12, tzinfo=UTC)
    registry = InMemoryCapabilityRegistry(FrozenClock(instant))
    artifact = _artifact(valid_artifact_data)

    record = registry.publish(artifact)

    assert registry.ready()
    assert registry.get(artifact.capability.id, "1.0.0") == record
    assert record.published_at == instant
    assert record.content_hash.startswith("sha256:")


def test_identical_publish_is_idempotent(valid_artifact_data: dict[str, Any]) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    artifact = _artifact(valid_artifact_data)

    assert registry.publish(artifact) == registry.publish(artifact)


def test_nested_mutation_cannot_change_published_content(
    valid_artifact_data: dict[str, Any],
) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    artifact = _artifact(valid_artifact_data)
    published = registry.publish(artifact)
    artifact.inputs.properties.clear()
    published.artifact.outputs.properties.clear()
    fetched = registry.get(published.artifact.capability.id, "1.0.0")
    assert fetched.artifact.inputs.properties
    assert fetched.artifact.outputs.properties
    fetched.artifact.inputs.properties.clear()
    assert registry.latest(published.artifact.capability.id).artifact.inputs.properties


def test_version_cannot_be_overwritten(valid_artifact_data: dict[str, Any]) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    original = _artifact(valid_artifact_data)
    registry.publish(original)
    changed = original.model_copy(
        update={"capability": original.capability.model_copy(update={"name": "Changed"})}
    )

    with pytest.raises(CapabilityConflictError, match="immutable"):
        registry.publish(changed)


def test_declared_hash_must_match_content(valid_artifact_data: dict[str, Any]) -> None:
    artifact = _artifact(valid_artifact_data)
    artifact = artifact.model_copy(
        update={
            "provenance": artifact.provenance.model_copy(
                update={"artifact_content_hash": "sha256:" + "0" * 64}
            )
        }
    )
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))

    with pytest.raises(CapabilityIntegrityError, match="does not match"):
        registry.publish(artifact)


def test_latest_and_versions_use_semantic_order(valid_artifact_data: dict[str, Any]) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    for version in ("1.9.0", "1.10.0", "2.0.0"):
        registry.publish(_artifact(valid_artifact_data, version))

    versions = registry.versions("member.lookup_savings_balance")

    assert [record.artifact.capability.version for record in versions] == [
        "2.0.0",
        "1.10.0",
        "1.9.0",
    ]
    assert registry.latest("member.lookup_savings_balance").artifact.capability.version == "2.0.0"


def test_publish_next_atomically_versions_and_rehashes(valid_artifact_data: dict[str, Any]) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    original = _artifact(valid_artifact_data, "1.0.0")
    registry.publish(original)

    published = registry.publish_next(original)

    assert published.artifact.capability.version == "1.0.1"
    assert published.artifact.provenance.artifact_content_hash == published.content_hash
    assert registry.get(original.capability.id, "1.0.0").artifact == original


def test_publish_next_preserves_a_requested_newer_minor_version(
    valid_artifact_data: dict[str, Any],
) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    registry.publish(_artifact(valid_artifact_data, "3.1.0"))

    proposed = _artifact(valid_artifact_data, "3.2.0")
    first = registry.publish_next(proposed)
    second = registry.publish_next(proposed)

    assert first.artifact.capability.version == "3.2.0"
    assert second.artifact.capability.version == "3.2.1"


@pytest.mark.parametrize("operation", ["get", "latest", "versions"])
def test_unknown_capability_is_safe_not_found(operation: str) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))

    with pytest.raises(CapabilityNotFoundError, match="not found"):
        if operation == "get":
            registry.get("member.unknown", "1.0.0")
        elif operation == "latest":
            registry.latest("member.unknown")
        else:
            registry.versions("member.unknown")


def test_local_registry_publishes_and_reloads_from_a_fresh_instance(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    artifact = _artifact(valid_artifact_data)
    first = LocalCapabilityRegistry(tmp_path)

    published = first.publish(artifact)
    reloaded = LocalCapabilityRegistry(tmp_path).get(artifact.capability.id, "1.0.0")

    assert reloaded.artifact == published.artifact
    assert reloaded.content_hash == published.content_hash
    assert (tmp_path / artifact.capability.id / "1.0.0.yaml").is_file()
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_local_publish_next_versions_against_durable_state(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    artifact = _artifact(valid_artifact_data)
    first = LocalCapabilityRegistry(tmp_path).publish_next(artifact)
    second = LocalCapabilityRegistry(tmp_path).publish_next(artifact)

    assert first.artifact.capability.version == "1.0.0"
    assert second.artifact.capability.version == "1.0.1"
    assert second.artifact.provenance.artifact_content_hash == second.content_hash


def test_local_registry_never_overwrites_an_immutable_version(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    artifact = _artifact(valid_artifact_data)
    registry = LocalCapabilityRegistry(tmp_path)
    registry.publish(artifact)
    changed = artifact.model_copy(
        update={"capability": artifact.capability.model_copy(update={"name": "Changed"})}
    )

    with pytest.raises(CapabilityConflictError, match="immutable"):
        registry.publish(changed)

    assert (
        LocalCapabilityRegistry(tmp_path).get(artifact.capability.id, "1.0.0").artifact == artifact
    )


def test_local_registry_rejects_tampered_and_misplaced_artifacts(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    artifact = _artifact(valid_artifact_data)
    registry = LocalCapabilityRegistry(tmp_path)
    published = registry.publish_next(artifact)
    path = tmp_path / artifact.capability.id / "1.0.0.yaml"
    path.write_text(path.read_text().replace(artifact.capability.name, "Changed"))

    with pytest.raises(CapabilityIntegrityError, match="hash"):
        registry.get(artifact.capability.id, "1.0.0")

    misplaced_root = tmp_path / "misplaced"
    misplaced = misplaced_root / "member.wrong" / "1.0.0.yaml"
    misplaced.parent.mkdir(parents=True)
    original = LocalCapabilityRegistry(tmp_path / "original")
    original.publish(published.artifact)
    source = tmp_path / "original" / artifact.capability.id / "1.0.0.yaml"
    misplaced.write_text(source.read_text())
    with pytest.raises(CapabilityIntegrityError, match="storage path"):
        LocalCapabilityRegistry(misplaced_root).all()


def test_local_registry_rejects_oversized_artifact(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    with pytest.raises(CapabilityIntegrityError, match="size limit"):
        LocalCapabilityRegistry(tmp_path, maximum_artifact_bytes=10).publish(
            _artifact(valid_artifact_data)
        )
    assert not tuple(tmp_path.rglob("*.yaml"))


def test_local_registry_rejects_malformed_stored_artifact(tmp_path: Path) -> None:
    path = tmp_path / "member.invalid" / "1.0.0.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: [")

    with pytest.raises(CapabilityIntegrityError, match="schema validation"):
        LocalCapabilityRegistry(tmp_path).all()


def test_failed_local_publication_leaves_no_visible_or_temporary_artifact(
    tmp_path: Path,
    valid_artifact_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("replayforge.capabilities.registry.os.link", fail_link)

    with pytest.raises(CapabilityPublicationError, match="storage is unavailable"):
        LocalCapabilityRegistry(tmp_path).publish(_artifact(valid_artifact_data))

    assert not tuple(tmp_path.rglob("*.yaml"))
    assert not tuple(tmp_path.rglob("*.tmp"))


def test_concurrent_local_publishers_allocate_distinct_versions(
    tmp_path: Path, valid_artifact_data: dict[str, Any]
) -> None:
    artifact = _artifact(valid_artifact_data)

    def publish() -> str:
        record = LocalCapabilityRegistry(tmp_path).publish_next(artifact)
        return record.artifact.capability.version

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = tuple(executor.map(lambda _: publish(), range(2)))

    assert set(versions) == {"1.0.0", "1.0.1"}
