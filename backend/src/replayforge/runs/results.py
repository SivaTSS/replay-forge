"""Discriminated terminal results for discovery and deterministic replay."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

_RUN_ID = r"^run_[0-9a-f]{32}$"
_INTERVENTION_ID = r"^int_[0-9a-f]{32}$"
_CAPABILITY_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
_SEMANTIC_VERSION = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_STABLE_CODE = r"^[a-z][a-z0-9_]{0,63}$"
_STEP_ID = r"^[a-z][a-z0-9_.-]+$"
_EVIDENCE_KEY = r"^evidence://[^\s]+$"


class ResultModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class CapabilityReference(ResultModel):
    id: str = Field(pattern=_CAPABILITY_ID)
    version: str = Field(pattern=_SEMANTIC_VERSION)


class VerifiedCheckpoint(ResultModel):
    id: str = Field(pattern=_STABLE_CODE)
    verified: Literal[True]


class CompletedResultModel(ResultModel):
    run_id: str = Field(pattern=_RUN_ID)
    evidence_manifest: str = Field(pattern=_EVIDENCE_KEY)

    @model_validator(mode="after")
    def validate_evidence_ownership(self) -> CompletedResultModel:
        if not self.evidence_manifest.startswith(f"evidence://{self.run_id}/"):
            raise ValueError("result evidence manifest must belong to its run")
        return self


class SuccessResult(CompletedResultModel):
    status: Literal["success"]
    capability: CapabilityReference
    outputs: dict[str, JsonValue]
    checkpoint: VerifiedCheckpoint


class BusinessOutcomeResult(CompletedResultModel):
    status: Literal["business_outcome"]
    code: str = Field(pattern=_STABLE_CODE)
    details: dict[str, JsonValue]


class FailureResult(CompletedResultModel):
    status: Literal["failure"]
    code: str = Field(pattern=_STABLE_CODE)
    message: str = Field(min_length=1, max_length=1_000)
    recoverable: bool
    step_id: str | None = Field(default=None, pattern=_STEP_ID)
    expected: dict[str, JsonValue] | None = None
    observed: dict[str, JsonValue] | None = None


class InterventionRequiredResult(ResultModel):
    status: Literal["intervention_required"]
    run_id: str = Field(pattern=_RUN_ID)
    intervention_id: str = Field(pattern=_INTERVENTION_ID)
    code: str = Field(pattern=_STABLE_CODE)
    session_live: Literal[True]
    control_owner: Literal["automation_paused"]
    step_id: str | None = Field(default=None, pattern=_STEP_ID)


RunResult = Annotated[
    SuccessResult | BusinessOutcomeResult | FailureResult | InterventionRequiredResult,
    Field(discriminator="status"),
]
