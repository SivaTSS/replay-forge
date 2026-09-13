"""Framework-independent inputs and outputs for policy evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from replayforge.policy.types import RISK_RANK, DataClassification, Decision, Risk
from replayforge.shared.ids import EntityId, EntityKind, parse_id


class PrincipalType(StrEnum):
    AUTOMATION = "automation"
    HUMAN = "human"


class RunMode(StrEnum):
    DISCOVERY = "discovery"
    REPLAY = "replay"
    HUMAN_CONTROL = "human_control"


@dataclass(frozen=True, slots=True)
class PolicyLayer:
    """One independently managed allowlist; every field is restrictive."""

    name: str
    allowed_origins: frozenset[str]
    allowed_route_patterns: frozenset[str]
    allowed_action_types: frozenset[str]
    maximum_risk: Risk
    forbidden_field_classes: frozenset[DataClassification] = frozenset(
        {DataClassification.CREDENTIAL, DataClassification.SECRET}
    )

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", self.name) is None:
            raise ValueError("policy layer name must be a stable identifier")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("policy origins must be credential-free HTTP origins")
        for pattern in self.allowed_route_patterns:
            if (
                not pattern.startswith("/")
                or "?" in pattern
                or "#" in pattern
                or "//" in pattern
                or ".." in pattern
            ):
                raise ValueError("policy route patterns must be absolute paths")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_]*", action) is None for action in self.allowed_action_types
        ):
            raise ValueError("policy action types must be stable identifiers")


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    layer_names: tuple[str, ...]
    allowed_origins: frozenset[str]
    allowed_route_patterns: frozenset[str]
    allowed_action_types: frozenset[str]
    maximum_risk: Risk
    forbidden_field_classes: frozenset[DataClassification]

    @classmethod
    def intersect(cls, *layers: PolicyLayer) -> EffectivePolicy:
        if not layers:
            raise ValueError("at least one policy layer is required")
        return cls(
            layer_names=tuple(layer.name for layer in layers),
            allowed_origins=frozenset.intersection(*(layer.allowed_origins for layer in layers)),
            allowed_route_patterns=frozenset.intersection(
                *(layer.allowed_route_patterns for layer in layers)
            ),
            allowed_action_types=frozenset.intersection(
                *(layer.allowed_action_types for layer in layers)
            ),
            maximum_risk=min((layer.maximum_risk for layer in layers), key=RISK_RANK.__getitem__),
            forbidden_field_classes=frozenset.union(
                *(layer.forbidden_field_classes for layer in layers)
            ),
        )


@dataclass(frozen=True, slots=True)
class ActionContext:
    principal_type: PrincipalType
    principal_id: str
    run_mode: RunMode
    application_family: str
    tenant: str
    origin: str
    route: str
    action_type: str
    target_description: str
    declared_risk: Risk
    control_owner: str
    field_classification: DataClassification | None = None
    registered_target_risk: Risk | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    id: EntityId
    decision: Decision
    reason_code: str
    explanation: str
    matched_layers: tuple[str, ...]
    effective_risk: Risk
    required_evidence: tuple[str, ...]
    redaction_directives: tuple[str, ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        parse_id(self.id, EntityKind.DECISION)
        if re.fullmatch(r"[a-z][a-z0-9_]*", self.reason_code) is None:
            raise ValueError("policy reason code must be a stable identifier")
        if not self.explanation.strip():
            raise ValueError("policy explanation cannot be empty")
        for name, values in (
            ("matched layers", self.matched_layers),
            ("required evidence", self.required_evidence),
            ("redaction directives", self.redaction_directives),
        ):
            if (
                not values
                or len(values) != len(set(values))
                or any(not value.strip() for value in values)
            ):
                raise ValueError(f"policy {name} must be non-empty and unique")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("policy evaluation timestamp must include an offset")
