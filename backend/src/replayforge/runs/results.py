"""Discriminated terminal results for discovery and deterministic replay."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityReference(ResultModel):
    id: str
    version: str


class VerifiedCheckpoint(ResultModel):
    id: str
    verified: Literal[True]


class SuccessResult(ResultModel):
    status: Literal["success"]
    run_id: str
    capability: CapabilityReference
    outputs: dict[str, Any]
    checkpoint: VerifiedCheckpoint
    evidence_manifest: str


class BusinessOutcomeResult(ResultModel):
    status: Literal["business_outcome"]
    run_id: str
    code: str
    details: dict[str, Any]
    evidence_manifest: str


class FailureResult(ResultModel):
    status: Literal["failure"]
    run_id: str
    code: str
    message: str
    recoverable: bool
    evidence_manifest: str
    step_id: str | None = None
    expected: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None


class InterventionRequiredResult(ResultModel):
    status: Literal["intervention_required"]
    run_id: str
    intervention_id: str
    code: str
    session_live: Literal[True]
    control_owner: Literal["automation_paused"]
    step_id: str | None = None


RunResult = Annotated[
    SuccessResult | BusinessOutcomeResult | FailureResult | InterventionRequiredResult,
    Field(discriminator="status"),
]
