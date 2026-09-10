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
