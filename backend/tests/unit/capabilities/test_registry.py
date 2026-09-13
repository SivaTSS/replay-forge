from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import (
    CapabilityConflictError,
    CapabilityIntegrityError,
    CapabilityNotFoundError,
    InMemoryCapabilityRegistry,
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
    assert registry.get(artifact.capability.id, "1.0.0") is record
    assert record.published_at == instant
    assert record.content_hash.startswith("sha256:")


def test_identical_publish_is_idempotent(valid_artifact_data: dict[str, Any]) -> None:
    registry = InMemoryCapabilityRegistry(FrozenClock(datetime.now(UTC)))
    artifact = _artifact(valid_artifact_data)

    assert registry.publish(artifact) is registry.publish(artifact)


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
