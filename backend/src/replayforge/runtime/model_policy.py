"""Reviewed, file-backed policy for bounded model use."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

_MAX_POLICY_BYTES = 64 * 1024


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Literal["USD"]
    unit_tokens: Literal[1_000_000]
    input_per_unit: Decimal = Field(ge=0, le=Decimal("1.00"))
    output_per_unit: Decimal = Field(ge=0, le=Decimal("5.00"))
    verified_on: date
    source: HttpUrl


class ModelPolicy(BaseModel):
    """Cost and execution limits that cannot be overridden per invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    provider: Literal["openai"]
    model: Literal["gpt-5.6-luna"]
    reasoning_effort: Literal["low"]
    max_output_tokens: int = Field(ge=256, le=1_000)
    max_model_calls_per_run: int = Field(ge=1, le=12)
    timeout_seconds: float = Field(ge=1, le=60)
    max_frame_bytes: int = Field(ge=64 * 1024, le=2 * 1024 * 1024)
    pricing: ModelPricing

    @model_validator(mode="after")
    def require_official_pricing_source(self) -> ModelPolicy:
        if self.pricing.source.host != "developers.openai.com":
            raise ValueError("model pricing must cite official OpenAI documentation")
        maximum_output_cost = (
            Decimal(self.max_model_calls_per_run)
            * Decimal(self.max_output_tokens)
            * self.pricing.output_per_unit
            / Decimal(self.pricing.unit_tokens)
        )
        if maximum_output_cost > Decimal("0.01"):
            raise ValueError("model policy exceeds the one-cent maximum output budget")
        return self


def load_model_policy(path: Path) -> ModelPolicy:
    if not path.is_file():
        raise ValueError("model policy file does not exist")
    content = path.read_bytes()
    if not content or len(content) > _MAX_POLICY_BYTES:
        raise ValueError("model policy file is empty or exceeds 64 KiB")
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError("model policy is not valid YAML") from error
    if not isinstance(payload, dict):
        raise ValueError("model policy root must be a mapping")
    return ModelPolicy.model_validate(payload)
