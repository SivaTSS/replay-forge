"""Framework-independent inputs and outputs for policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from replayforge.policy.types import RISK_RANK, DataClassification, Decision, Risk
from replayforge.shared.ids import EntityId


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
