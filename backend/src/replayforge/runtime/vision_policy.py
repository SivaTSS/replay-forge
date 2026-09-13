"""Reviewed, file-backed policy for deterministic visual grounding."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_POLICY_BYTES = 64 * 1024


class _VisionPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisionOcrPolicy(_VisionPolicyModel):
    minimum_confidence: float = Field(ge=0, le=1)


class VisionPhrasePolicy(_VisionPolicyModel):
    maximum_line_gap_in_text_heights: float = Field(gt=0, le=10)


class VisionSegmentationPolicy(_VisionPolicyModel):
    minimum_component_area_ratio: float = Field(gt=0, lt=1)
    maximum_component_area_ratio: float = Field(gt=0, le=1)
    maximum_components: int = Field(gt=0, le=20_000)

    @model_validator(mode="after")
    def validate_component_area(self) -> VisionSegmentationPolicy:
        if self.minimum_component_area_ratio >= self.maximum_component_area_ratio:
            raise ValueError("minimum component area must be less than maximum component area")
        return self


class VisionImagePolicy(_VisionPolicyModel):
    canonical_width: int = Field(gt=0, le=512)
    canonical_height: int = Field(gt=0, le=512)
    minimum_similarity: float = Field(ge=0, le=1)
    uniqueness_margin: float = Field(ge=0, le=1)


class VisionBudgetPolicy(_VisionPolicyModel):
    maximum_frame_pixels: int = Field(gt=0, le=32_000_000)
    maximum_grounding_milliseconds: int = Field(gt=0, le=30_000)


class VisionGroundingPolicy(_VisionPolicyModel):
    """Global visual limits; capabilities cannot override these values."""

    schema_version: Literal["1.0"]
    ocr: VisionOcrPolicy
    phrases: VisionPhrasePolicy
    segmentation: VisionSegmentationPolicy
    image: VisionImagePolicy
    budgets: VisionBudgetPolicy


def load_vision_policy(path: Path) -> VisionGroundingPolicy:
    if not path.is_file():
        raise ValueError("vision policy file does not exist")
    content = path.read_bytes()
    if not content or len(content) > _MAX_POLICY_BYTES:
        raise ValueError("vision policy file is empty or exceeds 64 KiB")
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError("vision policy is not valid YAML") from error
    if not isinstance(payload, dict):
        raise ValueError("vision policy root must be a mapping")
    return VisionGroundingPolicy.model_validate(payload)
