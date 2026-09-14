"""Application registration repository and safe YAML loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

import yaml

from replayforge.applications.models import ApplicationRegistration, SurfaceLaunch
from replayforge.shared.yaml import load_unique_yaml


class ApplicationRegistry(Protocol):
    def ready(self) -> bool: ...

    def get(self, application_family: str) -> ApplicationRegistration: ...

    def all(self) -> tuple[ApplicationRegistration, ...]: ...

    def resolve(self, application_family: str, tenant: str, entry_point: str) -> SurfaceLaunch: ...


@dataclass(slots=True)
class InMemoryApplicationRegistry:
    registrations: dict[str, ApplicationRegistration]
    _lock: Lock = field(default_factory=Lock, init=False)

    def __post_init__(self) -> None:
        if not self.registrations:
            raise ValueError("application registry cannot be empty")
        if set(self.registrations) != {
            registration.application_family for registration in self.registrations.values()
        }:
            raise ValueError("application registry keys must match application families")
        self.registrations = {
            key: registration.model_copy(deep=True)
            for key, registration in self.registrations.items()
        }

    def ready(self) -> bool:
        return True

    def get(self, application_family: str) -> ApplicationRegistration:
        with self._lock:
            try:
                return self.registrations[application_family].model_copy(deep=True)
            except KeyError as exc:
                raise ValueError("application family is not registered") from exc

    def all(self) -> tuple[ApplicationRegistration, ...]:
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self.registrations.values())

    def resolve(self, application_family: str, tenant: str, entry_point: str) -> SurfaceLaunch:
        return self.get(application_family).resolve(tenant, entry_point)


def load_application_registry(path: Path) -> ApplicationRegistry:
    if not path.is_file():
        raise ValueError("application registry file does not exist")
    if path.stat().st_size > 1_000_000:
        raise ValueError("application registry exceeds the one-megabyte startup limit")
    try:
        raw = load_unique_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("application registry is not valid safe YAML") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise ValueError("application registry must declare schema version 1.0")
    if raw.keys() - {"schema_version", "applications"}:
        raise ValueError("application registry contains unknown fields")
    entries = raw.get("applications")
    if not isinstance(entries, list) or not entries:
        raise ValueError("application registry must contain applications")
    registrations = {
        registration.application_family: registration
        for registration in (ApplicationRegistration.model_validate(item) for item in entries)
    }
    if len(registrations) != len(entries):
        raise ValueError("application families must be unique")
    return InMemoryApplicationRegistry(registrations)
