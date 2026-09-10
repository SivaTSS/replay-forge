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
