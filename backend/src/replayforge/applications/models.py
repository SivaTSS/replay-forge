"""Reviewable registration data for an application surface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from replayforge.capabilities.models import Landmark, SurfaceKind
from replayforge.policy.types import DataClassification, Risk

_WEB_ACTION_TYPES = frozenset(
    {
        "navigate",
        "type",
        "click",
        "select",
        "press_keys",
        "scroll",
        "wait_for",
        "assert",
        "extract",
        "switch_context",
    }
)


class ApplicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class EntryPointRegistration(ApplicationModel):
    """A symbolic launch location inside an approved origin."""

    path_template: str = Field(pattern=r"^/[A-Za-z0-9_./{}-]*$")
    rendered_surface: bool = False
    readiness_frame_title: str | None = Field(default=None, min_length=1, max_length=200)
    required_landmarks: tuple[Landmark, ...] = ()
    forbidden_landmarks: tuple[Landmark, ...] = ()

    @model_validator(mode="after")
    def validate_template(self) -> Self:
        variables = set(re.findall(r"\{([^{}]+)\}", self.path_template))
        if variables - {"tenant"}:
            raise ValueError("entry-point templates may only contain the tenant variable")
        if self.path_template.count("{tenant}") > 1:
            raise ValueError("entry-point templates may contain tenant only once")
        if "//" in self.path_template or ".." in self.path_template:
            raise ValueError("entry-point path template contains an unsafe path")
        return self


class ApplicationPolicy(ApplicationModel):
    allowed_route_patterns: frozenset[str] = Field(min_length=1)
    allowed_action_types: frozenset[str] = Field(min_length=1)
    maximum_risk: Risk
    forbidden_field_classes: frozenset[DataClassification] = frozenset(
        {DataClassification.CREDENTIAL, DataClassification.SECRET}
    )

    @model_validator(mode="after")
    def validate_routes(self) -> Self:
        unsupported_actions = self.allowed_action_types - _WEB_ACTION_TYPES
        if unsupported_actions:
            raise ValueError("application policy contains unsupported web actions")
        required_forbidden = {
            DataClassification.CREDENTIAL,
            DataClassification.SECRET,
        }
        if not required_forbidden.issubset(self.forbidden_field_classes):
            raise ValueError("application policy must forbid credentials and secrets")
        for pattern in self.allowed_route_patterns:
            if not pattern.startswith("/") or "?" in pattern or "#" in pattern:
                raise ValueError("approved route patterns must be absolute paths")
            if "//" in pattern or ".." in pattern:
                raise ValueError("approved route patterns contain an unsafe path")
        return self


class ApplicationRegistration(ApplicationModel):
    schema_version: Literal["1.0"] = "1.0"
    application_family: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    capability_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    origin: str
    surface: SurfaceKind
    surface_contract: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    base_variant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    tenants: tuple[str, ...] = Field(min_length=1)
    entry_points: dict[str, EntryPointRegistration] = Field(min_length=1)
    route_aliases: dict[str, str] = Field(default_factory=dict)
    policy: ApplicationPolicy

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("application origin must be a credential-free HTTP origin")
        if len(set(self.tenants)) != len(self.tenants):
            raise ValueError("application tenants must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", tenant) for tenant in self.tenants):
            raise ValueError("application tenants have an invalid identifier")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) for name in self.entry_points):
            raise ValueError("entry-point identifiers have an invalid format")
        supported_contracts = {"web": {"web.v1"}, "desktop": set()}
        if (
            self.surface.value not in supported_contracts
            or self.surface_contract not in supported_contracts[self.surface.value]
        ):
            raise ValueError("surface contract is not supported by a registered adapter")
        if any(source == destination for source, destination in self.route_aliases.items()):
            raise ValueError("route aliases must not map a route to itself")
        for source, destination in self.route_aliases.items():
            for route in (source, destination):
                if (
                    not route.startswith("/")
                    or "?" in route
                    or "#" in route
                    or "//" in route
                    or ".." in route
                ):
                    raise ValueError("route aliases must be absolute paths")
            if not any(
                _route_matches(destination, pattern)
                for pattern in self.policy.allowed_route_patterns
            ):
                raise ValueError("route alias destination is outside the approved route policy")
        return self

    def resolve(self, tenant: str, entry_point: str) -> SurfaceLaunch:
        if tenant not in self.tenants:
            raise ValueError("tenant is not registered for the application")
        try:
            entry = self.entry_points[entry_point]
        except KeyError as exc:
            raise ValueError("entry point is not registered for the application") from exc
        path = entry.path_template.replace("{tenant}", quote(tenant, safe=""))
        if not path.startswith("/") or "//" in path or ".." in path:
            raise ValueError("resolved entry point is unsafe")
        return SurfaceLaunch(
            application_family=self.application_family,
            tenant=tenant,
            entry_point=entry_point,
            url=f"{self.origin.rstrip('/')}{path}",
            surface=self.surface,
            surface_contract=self.surface_contract,
            rendered_surface=entry.rendered_surface,
            required_landmarks=entry.required_landmarks,
            forbidden_landmarks=entry.forbidden_landmarks,
            readiness_frame_title=entry.readiness_frame_title,
            entry_points={
                name: self._entry_url(tenant, registration.path_template)
                for name, registration in self.entry_points.items()
            },
        )

    def _entry_url(self, tenant: str, template: str) -> str:
        resolved = template.replace("{tenant}", quote(tenant, safe=""))
        return f"{self.origin.rstrip('/')}{resolved}"

    def normalize_route(self, route: str) -> str:
        return self.route_aliases.get(route, route)


def _route_matches(route: str, pattern: str) -> bool:
    if not route.startswith("/") or "?" in route or "#" in route:
        return False
    route_parts = route.strip("/").split("/") if route != "/" else []
    pattern_parts = pattern.strip("/").split("/") if pattern != "/" else []
    if len(route_parts) != len(pattern_parts):
        return False
    return all(
        pattern_part == "*" or pattern_part.startswith(":") or pattern_part == route_part
        for route_part, pattern_part in zip(route_parts, pattern_parts, strict=True)
    )


@dataclass(frozen=True, slots=True)
class SurfaceLaunch:
    application_family: str
    tenant: str
    entry_point: str
    url: str
    surface: SurfaceKind
    surface_contract: str
    rendered_surface: bool
    required_landmarks: tuple[Landmark, ...]
    forbidden_landmarks: tuple[Landmark, ...]
    readiness_frame_title: str | None
    entry_points: dict[str, str]
