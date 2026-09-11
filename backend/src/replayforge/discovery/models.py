"""Provider-neutral discovery proposals, recordings, and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from replayforge.capabilities.models import Action, CapabilityArtifact, LocatorBundle
from replayforge.policy.types import Risk
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
    declared_risk: Risk
    confidence: float = Field(ge=0, le=1)


class CompleteProposal(DiscoveryModel):
    kind: Literal["complete"]
    rationale: str = Field(min_length=1, max_length=500)


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
    required_output_names: tuple[str, ...]
    maximum_risk: Risk = Risk.READ_ONLY
