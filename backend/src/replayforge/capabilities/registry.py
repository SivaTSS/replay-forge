"""Immutable capability registry contracts and an in-memory adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Protocol

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.shared.clock import Clock


class CapabilityNotFoundError(KeyError):
    """The requested capability identity or exact version is unknown."""


class CapabilityConflictError(RuntimeError):
    """An immutable capability version already exists with different content."""


class CapabilityIntegrityError(ValueError):
    """Artifact-declared integrity metadata does not match canonical content."""


@dataclass(frozen=True, slots=True)
class CapabilityVersionRecord:
    artifact: CapabilityArtifact
    content_hash: str
    published_at: datetime


class CapabilityRegistry(Protocol):
    def ready(self) -> bool: ...

    def publish(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord: ...

    def publish_next(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord: ...

    def get(self, capability_id: str, version: str) -> CapabilityVersionRecord: ...

    def latest(self, capability_id: str) -> CapabilityVersionRecord: ...

    def versions(self, capability_id: str) -> tuple[CapabilityVersionRecord, ...]: ...


def _semantic_version(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


@dataclass(slots=True)
class InMemoryCapabilityRegistry:
    """Thread-safe contract adapter preserving immutable version semantics."""

    clock: Clock
    _records: dict[tuple[str, str], CapabilityVersionRecord] = field(
        init=False, default_factory=dict
    )
    _lock: Lock = field(init=False, default_factory=Lock)

    def ready(self) -> bool:
        return True

    def publish(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord:
        content_hash = artifact_content_hash(artifact)
        declared_hash = artifact.provenance.artifact_content_hash
        if declared_hash is not None and declared_hash != content_hash:
            raise CapabilityIntegrityError(
                "artifact provenance hash does not match canonical artifact content"
            )

        key = (artifact.capability.id, artifact.capability.version)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise CapabilityConflictError(
                        "capability versions are immutable and cannot be overwritten"
                    )
                return existing
            record = CapabilityVersionRecord(
                artifact=artifact,
                content_hash=content_hash,
                published_at=self.clock.now(),
            )
            self._records[key] = record
            return record

    def publish_next(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord:
        capability_id = artifact.capability.id
        with self._lock:
            registered_versions = [
                _semantic_version(version)
                for registered_id, version in self._records
                if registered_id == capability_id
            ]
            proposed = _semantic_version(artifact.capability.version)
            if registered_versions:
                latest = max(registered_versions)
                version = (latest[0], latest[1], latest[2] + 1)
            else:
                version = proposed
            version_text = ".".join(str(part) for part in version)
            candidate = artifact.model_copy(
                update={
                    "capability": artifact.capability.model_copy(update={"version": version_text}),
                    "provenance": artifact.provenance.model_copy(
                        update={"artifact_content_hash": None}
                    ),
                }
            )
            content_hash = artifact_content_hash(candidate)
            candidate = candidate.model_copy(
                update={
                    "provenance": candidate.provenance.model_copy(
                        update={"artifact_content_hash": content_hash}
                    )
                }
            )
            record = CapabilityVersionRecord(candidate, content_hash, self.clock.now())
            self._records[(capability_id, version_text)] = record
            return record

    def get(self, capability_id: str, version: str) -> CapabilityVersionRecord:
        with self._lock:
            try:
                return self._records[(capability_id, version)]
            except KeyError as error:
                raise CapabilityNotFoundError(
                    "capability or exact version was not found"
                ) from error

    def latest(self, capability_id: str) -> CapabilityVersionRecord:
        records = self.versions(capability_id)
        return max(
            records, key=lambda record: _semantic_version(record.artifact.capability.version)
        )

    def versions(self, capability_id: str) -> tuple[CapabilityVersionRecord, ...]:
        with self._lock:
            records = tuple(
                record
                for (registered_id, _), record in self._records.items()
                if registered_id == capability_id
            )
        if not records:
            raise CapabilityNotFoundError("capability was not found")
        return tuple(
            sorted(
                records,
                key=lambda record: _semantic_version(record.artifact.capability.version),
                reverse=True,
            )
        )
