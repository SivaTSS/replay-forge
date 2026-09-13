"""Application registration repository and safe YAML loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from replayforge.applications.models import ApplicationRegistration, SurfaceLaunch


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(  # type: ignore[no-untyped-call]
            value_node, deep=deep
        )
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


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

    def ready(self) -> bool:
        return True

    def get(self, application_family: str) -> ApplicationRegistration:
        with self._lock:
            try:
                return self.registrations[application_family]
            except KeyError as exc:
                raise ValueError("application family is not registered") from exc

    def all(self) -> tuple[ApplicationRegistration, ...]:
        with self._lock:
            return tuple(self.registrations.values())

    def resolve(self, application_family: str, tenant: str, entry_point: str) -> SurfaceLaunch:
        return self.get(application_family).resolve(tenant, entry_point)


def load_application_registry(path: Path) -> ApplicationRegistry:
    if not path.is_file():
        raise ValueError("application registry file does not exist")
    if path.stat().st_size > 1_000_000:
        raise ValueError("application registry exceeds the one-megabyte startup limit")
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("application registry is not valid safe YAML") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise ValueError("application registry must declare schema version 1.0")
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


def default_application_registry(origin: str) -> ApplicationRegistry:
    """Compatibility registration used by direct adapter tests without runtime settings."""
    registration = ApplicationRegistration.model_validate(
        {
            "application_family": "northstar_member_service",
            "capability_namespace": "member",
            "origin": origin,
            "surface": "web",
            "surface_contract": "web.v1",
            "base_variant": "standard",
            "tenants": ["harbor", "summit"],
            "entry_points": {
                "member_search": {
                    "path_template": "/{tenant}",
                    "readiness_frame_title": "Member operations",
                    "required_landmarks": [{"kind": "heading", "value": "Member Search"}],
                },
                "visual_member_search": {
                    "path_template": "/{tenant}/visual-terminal",
                    "rendered_surface": True,
                },
                "visual_member_workbench": {
                    "path_template": "/{tenant}/visual-workbench",
                    "rendered_surface": True,
                },
            },
            "route_aliases": {
                "/member-search": "/members/search",
                "/member-results": "/members/search",
                "/visual-terminal": "/members/search",
                "/visual-workbench": "/members/search",
            },
            "policy": {
                "allowed_route_patterns": [
                    "/members/search",
                    "/accounts/:account_id/details",
                ],
                "allowed_action_types": [
                    "navigate",
                    "type",
                    "click",
                    "select",
                    "press_keys",
                    "scroll",
                    "extract",
                    "wait_for",
                    "assert",
                    "switch_context",
                ],
                "maximum_risk": "sensitive",
            },
        }
    )
    return InMemoryApplicationRegistry({registration.application_family: registration})
