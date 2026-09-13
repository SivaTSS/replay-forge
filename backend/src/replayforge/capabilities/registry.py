"""Immutable capability registry contracts and local adapters."""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol

from pydantic import ValidationError

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import (
    ArtifactParseError,
    artifact_content_hash,
    dump_artifact_yaml,
    load_artifact_yaml,
)
from replayforge.shared.clock import Clock


class CapabilityNotFoundError(KeyError):
    """The requested capability identity or exact version is unknown."""


class CapabilityConflictError(RuntimeError):
    """An immutable capability version already exists with different content."""


class CapabilityIntegrityError(ValueError):
    """Artifact-declared integrity metadata does not match canonical content."""


class CapabilityPublicationError(RuntimeError):
    """A capability could not be durably read or published."""


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
                patch_version = (latest[0], latest[1], latest[2] + 1)
                version = max(proposed, patch_version)
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


@dataclass(slots=True)
class LocalCapabilityRegistry:
    """Atomic, immutable YAML registry for a single-host runtime.

    A process lock protects threads and a directory lock protects cooperating
    processes. Readers only observe complete files because publication links a
    fully flushed temporary file into the destination directory atomically.
    """

    root: Path
    maximum_artifact_bytes: int = 1_000_000
    _lock: Lock = field(init=False, default_factory=Lock)

    def __post_init__(self) -> None:
        if self.maximum_artifact_bytes < 1:
            raise ValueError("maximum artifact size must be positive")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()

    def ready(self) -> bool:
        return self.root.is_dir()

    def publish(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord:
        with self._write_lock():
            return self._publish_locked(artifact)

    def publish_next(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord:
        with self._write_lock():
            existing = self._versions_locked(artifact.capability.id)
            proposed = _semantic_version(artifact.capability.version)
            if existing:
                latest = max(
                    _semantic_version(record.artifact.capability.version) for record in existing
                )
                proposed = max(proposed, (latest[0], latest[1], latest[2] + 1))
            version = ".".join(str(part) for part in proposed)
            candidate = artifact.model_copy(
                update={
                    "capability": artifact.capability.model_copy(update={"version": version}),
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
            return self._publish_locked(candidate)

    def get(self, capability_id: str, version: str) -> CapabilityVersionRecord:
        path = self._path(capability_id, version)
        if not path.is_file():
            raise CapabilityNotFoundError("capability or exact version was not found")
        return self._load(path)

    def latest(self, capability_id: str) -> CapabilityVersionRecord:
        records = self.versions(capability_id)
        return max(
            records, key=lambda record: _semantic_version(record.artifact.capability.version)
        )

    def versions(self, capability_id: str) -> tuple[CapabilityVersionRecord, ...]:
        records = self._versions_locked(capability_id)
        if not records:
            raise CapabilityNotFoundError("capability was not found")
        return tuple(
            sorted(
                records,
                key=lambda record: _semantic_version(record.artifact.capability.version),
                reverse=True,
            )
        )

    def all(self) -> tuple[CapabilityVersionRecord, ...]:
        return tuple(self._load(path) for path in sorted(self.root.glob("*/*.yaml")))

    def _publish_locked(self, artifact: CapabilityArtifact) -> CapabilityVersionRecord:
        content_hash = artifact_content_hash(artifact)
        declared_hash = artifact.provenance.artifact_content_hash
        if declared_hash is not None and declared_hash != content_hash:
            raise CapabilityIntegrityError(
                "artifact provenance hash does not match canonical artifact content"
            )
        destination = self._path(artifact.capability.id, artifact.capability.version)
        if destination.exists():
            existing = self._load(destination)
            if existing.content_hash != content_hash:
                raise CapabilityConflictError(
                    "capability versions are immutable and cannot be overwritten"
                )
            return existing

        content = dump_artifact_yaml(artifact).encode("utf-8")
        if len(content) > self.maximum_artifact_bytes:
            raise ValueError("artifact exceeds the publication size limit")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._sync_directory(self.root)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
        except OSError as error:
            raise CapabilityPublicationError(
                "capability publication storage is unavailable"
            ) from error
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = self._load(destination)
                if existing.content_hash != content_hash:
                    raise CapabilityConflictError(
                        "capability versions are immutable and cannot be overwritten"
                    ) from None
                return existing
            self._sync_directory(destination.parent)
        except (CapabilityConflictError, CapabilityIntegrityError, CapabilityPublicationError):
            raise
        except OSError as error:
            raise CapabilityPublicationError(
                "capability publication storage is unavailable"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return self._load(destination)

    def _versions_locked(self, capability_id: str) -> tuple[CapabilityVersionRecord, ...]:
        directory = self._capability_directory(capability_id)
        if not directory.is_dir():
            return ()
        return tuple(self._load(path) for path in directory.glob("*.yaml"))

    def _load(self, path: Path) -> CapabilityVersionRecord:
        try:
            size = path.stat().st_size
        except FileNotFoundError as error:
            raise CapabilityNotFoundError("capability or exact version was not found") from error
        except OSError as error:
            raise CapabilityPublicationError("capability storage is unavailable") from error
        if size > self.maximum_artifact_bytes:
            raise CapabilityIntegrityError("artifact exceeds the loading size limit")
        try:
            artifact = load_artifact_yaml(path.read_text(encoding="utf-8"))
        except (ArtifactParseError, UnicodeError, ValidationError) as error:
            raise CapabilityIntegrityError("stored artifact failed schema validation") from error
        except OSError as error:
            raise CapabilityPublicationError("capability storage is unavailable") from error
        expected = self._path(artifact.capability.id, artifact.capability.version)
        if path.resolve() != expected:
            raise CapabilityIntegrityError("artifact identity does not match its storage path")
        content_hash = artifact_content_hash(artifact)
        declared_hash = artifact.provenance.artifact_content_hash
        if declared_hash is not None and declared_hash != content_hash:
            raise CapabilityIntegrityError(
                "artifact provenance hash does not match canonical artifact content"
            )
        published_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return CapabilityVersionRecord(artifact, content_hash, published_at)

    def _path(self, capability_id: str, version: str) -> Path:
        directory = self._capability_directory(capability_id)
        if not version or any(character not in "0123456789." for character in version):
            raise CapabilityNotFoundError("capability or exact version was not found")
        try:
            _semantic_version(version)
        except (TypeError, ValueError):
            raise CapabilityNotFoundError("capability or exact version was not found") from None
        path = (directory / f"{version}.yaml").resolve()
        if not path.is_relative_to(self.root):
            raise CapabilityNotFoundError("capability or exact version was not found")
        return path

    def _capability_directory(self, capability_id: str) -> Path:
        if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", capability_id) is None:
            raise CapabilityNotFoundError("capability was not found")
        directory = (self.root / capability_id).resolve()
        if not directory.is_relative_to(self.root):
            raise CapabilityNotFoundError("capability was not found")
        return directory

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        with self._lock:
            try:
                descriptor = os.open(self.root, os.O_RDONLY)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as error:
                if "descriptor" in locals():
                    os.close(descriptor)
                raise CapabilityPublicationError(
                    "capability publication storage is unavailable"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
