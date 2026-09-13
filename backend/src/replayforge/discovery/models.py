"""Provider-neutral discovery proposals, recordings, and results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from replayforge.capabilities.models import (
    Action,
    CapabilityArtifact,
    Condition,
    LocatorBundle,
    ObjectContract,
)
from replayforge.policy.types import DataClassification, Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult
from replayforge.surfaces.models import NormalizedObservation


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActProposal(DiscoveryModel):
    kind: Literal["act"]
    action: Action
    target: LocatorBundle | None = None
    rationale: str = Field(min_length=1, max_length=500)
    expected_effect: str = Field(min_length=1, max_length=500)
    expected_condition: Condition | None = None
    declared_risk: Risk
    confidence: float = Field(ge=0, le=1)


class CompleteProposal(DiscoveryModel):
    kind: Literal["complete"]
    rationale: str = Field(min_length=1, max_length=500)


class CapabilityDraftSpec(DiscoveryModel):
    """Provider-proposed task semantics, validated before any artifact is published."""

    operation_slug: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    capability_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_000)
    inputs: ObjectContract
    outputs: ObjectContract
    risk: Risk
    tags: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_contract_shape(self) -> Self:
        if not self.outputs.required:
            raise ValueError("capability drafts must declare at least one required output")
        for contract_name, contract in (("input", self.inputs), ("output", self.outputs)):
            names = tuple(contract.properties)
            if len(names) > 50:
                raise ValueError(f"capability drafts cannot declare more than 50 {contract_name}s")
            if len(set(contract.required)) != len(contract.required):
                raise ValueError(f"{contract_name} contract contains duplicate required fields")
            for name in names:
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
                    raise ValueError(f"{contract_name} field name is invalid")
                if any(
                    token in name.split("_")
                    for token in {"password", "secret", "token", "credential", "api", "key"}
                ):
                    raise ValueError(f"{contract_name} field name appears credential-related")
                schema = contract.properties[name]
                if schema.data_classification in {
                    DataClassification.CREDENTIAL,
                    DataClassification.SECRET,
                }:
                    raise ValueError(f"{contract_name} contract contains a forbidden field")
        return self

    @property
    def required_output_names(self) -> tuple[str, ...]:
        return self.outputs.required


@dataclass(frozen=True, slots=True)
class PlanningContext:
    goal: str
    inputs: dict[str, Any]
    observation: NormalizedObservation
    screenshot_png: bytes
    maximum_risk: Risk
    requested_capability_id: str | None = None
    application_family: str = ""
    entry_point: str = ""
    allowed_action_types: frozenset[str] = frozenset()


class EscalateProposal(DiscoveryModel):
    kind: Literal["escalate"]
    reason_code: str
    rationale: str = Field(min_length=1, max_length=500)


DiscoveryProposal = Annotated[
    ActProposal | CompleteProposal | EscalateProposal, Field(discriminator="kind")
]


@dataclass(frozen=True, slots=True)
class RecordedDiscoveryStep:
    action: Action
    target: LocatorBundle | None
    observation_before: NormalizedObservation
    observation_after: NormalizedObservation
    expected_effect: str
    rationale: str
    risk: Risk
    verified_postconditions: tuple[Condition, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoverySuccess:
    status: Literal["success"]
    run_id: str
    artifact: CapabilityArtifact
    evidence_manifest: str


DiscoveryResult = DiscoverySuccess | FailureResult | InterventionRequiredResult


@dataclass(frozen=True, slots=True)
class ProviderContext:
    goal: str
    inputs: dict[str, Any]
    observation: NormalizedObservation
    screenshot_png: bytes
    action_history: tuple[str, ...]
    allowed_action_types: frozenset[str]
    output_contract: ObjectContract
    captured_output_names: tuple[str, ...] = ()
    maximum_risk: Risk = Risk.READ_ONLY

    def __post_init__(self) -> None:
        captured = set(self.captured_output_names)
        if len(captured) != len(self.captured_output_names):
            raise ValueError("captured output names must be unique")
        if not captured.issubset(self.output_contract.required):
            raise ValueError("captured outputs must be declared by the output contract")

    @property
    def required_output_names(self) -> tuple[str, ...]:
        return self.output_contract.required

    @property
    def remaining_output_names(self) -> tuple[str, ...]:
        captured = set(self.captured_output_names)
        return tuple(name for name in self.required_output_names if name not in captured)
