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
    Step,
)
from replayforge.policy.types import DataClassification, Risk
from replayforge.runs.results import FailureResult
from replayforge.shared.ids import EntityKind, parse_id
from replayforge.surfaces.models import NormalizedObservation

_TARGETED_ACTIONS = frozenset({"click", "type", "select", "extract"})


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

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        requires_target = self.action.kind in _TARGETED_ACTIONS
        if requires_target and self.target is None:
            raise ValueError(f"{self.action.kind} action requires a target")
        if not requires_target and self.target is not None:
            raise ValueError(f"{self.action.kind} action cannot declare a target")
        return self


class CompleteProposal(DiscoveryModel):
    kind: Literal["complete"]
    rationale: str = Field(min_length=1, max_length=500)


class RecordedActionProposal(DiscoveryModel):
    """Model-selected reuse of a previously discovered action, re-grounded live."""

    kind: Literal["recorded_action"]
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    rationale: str = Field(min_length=1, max_length=500)
    expected_condition: Condition | None = None


class BranchProposal(DiscoveryModel):
    kind: Literal["branch"]
    condition: Condition
    rationale: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class ObservedBranch:
    after_step_count: int
    condition: Condition

    def __post_init__(self) -> None:
        if self.after_step_count < 1:
            raise ValueError("a branch requires at least one executed primary action")


@dataclass(frozen=True, slots=True)
class ScenarioContext:
    primary: CapabilityArtifact
    kind: Literal["business_outcome", "application_failure", "recovery"]


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
    observation_only: bool = False

    @model_validator(mode="after")
    def validate_contract_shape(self) -> Self:
        if not self.outputs.required and not self.observation_only:
            raise ValueError("capability drafts must declare at least one required output")
        for contract_name, contract in (("input", self.inputs), ("output", self.outputs)):
            names = tuple(contract.properties)
            if len(names) > 50:
                raise ValueError(f"capability drafts cannot declare more than 50 {contract_name}s")
            if len(set(contract.required)) != len(contract.required):
                raise ValueError(f"{contract_name} contract contains duplicate required fields")
            pending = list(contract.properties.items())
            while pending:
                name, schema = pending.pop()
                pending.extend(schema.properties.items())
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
                    raise ValueError(f"{contract_name} field name is invalid")
                if any(
                    token in name.split("_")
                    for token in {"password", "secret", "token", "credential", "api", "key"}
                ):
                    raise ValueError(f"{contract_name} field name appears credential-related")
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

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("discovery goal cannot be empty")
        if not self.screenshot_png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("planning context requires a PNG screenshot")
        if not self.application_family.strip() or not self.entry_point.strip():
            raise ValueError("planning context requires a registered application entry point")
        if not self.allowed_action_types:
            raise ValueError("planning context requires allowed action types")


class EscalateProposal(DiscoveryModel):
    kind: Literal["escalate"]
    reason_code: str
    rationale: str = Field(min_length=1, max_length=500)


DiscoveryProposal = Annotated[
    ActProposal | CompleteProposal | EscalateProposal | RecordedActionProposal | BranchProposal,
    Field(discriminator="kind"),
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

    def __post_init__(self) -> None:
        requires_target = self.action.kind in _TARGETED_ACTIONS
        if requires_target != (self.target is not None):
            requirement = "requires" if requires_target else "cannot declare"
            raise ValueError(f"{self.action.kind} action {requirement} a target")
        if not self.expected_effect.strip() or not self.rationale.strip():
            raise ValueError("recorded discovery step requires intent and rationale")


@dataclass(frozen=True, slots=True)
class DiscoverySuccess:
    status: Literal["success"]
    run_id: str
    artifact: CapabilityArtifact
    evidence_manifest: str
    branch: ObservedBranch | None = None

    def __post_init__(self) -> None:
        if self.status != "success":
            raise ValueError("discovery success status must be success")
        parse_id(self.run_id, EntityKind.RUN)
        if not self.evidence_manifest.startswith(f"evidence://{self.run_id}/"):
            raise ValueError("discovery evidence manifest must belong to its run")


DiscoveryResult = DiscoverySuccess | FailureResult


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
    previous_visual_text: tuple[str, ...] = ()
    reference_steps: tuple[Step, ...] = ()
    scenario_kind: Literal["business_outcome", "application_failure", "recovery"] | None = None
    branch_observed: bool = False
    recorded_step_count: int = 0
    rendered_surface: bool = False

    def __post_init__(self) -> None:
        captured = set(self.captured_output_names)
        if len(captured) != len(self.captured_output_names):
            raise ValueError("captured output names must be unique")
        if not captured.issubset(self.output_contract.properties):
            raise ValueError("captured outputs must be declared by the output contract")

    @property
    def required_output_names(self) -> tuple[str, ...]:
        return self.output_contract.required

    @property
    def remaining_output_names(self) -> tuple[str, ...]:
        captured = set(self.captured_output_names)
        return tuple(name for name in self.required_output_names if name not in captured)
