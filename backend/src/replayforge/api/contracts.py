"""Versioned HTTP request and response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplayInvocation(ApiModel):
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    version: str | None = Field(
        default=None, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
    )
    inputs: dict[str, Any]


class DiscoveryInvocation(ApiModel):
    goal: str = Field(min_length=10, max_length=1_000)
    application_family: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    entry_point: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    inputs: dict[str, Any]
    max_steps: int = Field(default=20, ge=1, le=50)
    timeout_seconds: int = Field(default=120, ge=10, le=600)


class ArtifactValidationRequest(ApiModel):
    yaml: str = Field(min_length=1, max_length=1_000_000)


class ArtifactValidationResponse(ApiModel):
    valid: bool
    capability_id: str
    version: str
    content_hash: str


class HealthResponse(ApiModel):
    status: str


class ErrorBody(ApiModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool
    details: list[dict[str, str]] = Field(default_factory=list)


class LeaseTransitionRequest(ApiModel):
    expected_lease_version: int = Field(ge=1)
    operator_id: str = Field(pattern=r"^[A-Za-z0-9_.@-]{2,100}$")


class TerminateInterventionRequest(ApiModel):
    expected_lease_version: int = Field(ge=1)
    operator_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.@-]{2,100}$")
    resolution: str = Field(min_length=3, max_length=500)


class InterventionTransitionResponse(ApiModel):
    intervention_id: str
    run_id: str
    session_id: str
    status: str
    control_owner: str
    lease_version: int
    lease_expires_at: str
