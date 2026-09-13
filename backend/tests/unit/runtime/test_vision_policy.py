from pathlib import Path

import pytest
from pydantic import ValidationError

from replayforge.runtime.vision_policy import VisionGroundingPolicy, load_vision_policy


def policy_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "ocr": {"minimum_confidence": 0.8},
        "phrases": {"maximum_line_gap_in_text_heights": 1.5},
        "segmentation": {
            "minimum_component_area_ratio": 0.00001,
            "maximum_component_area_ratio": 0.25,
            "maximum_components": 2000,
        },
        "image": {
            "canonical_width": 64,
            "canonical_height": 64,
            "minimum_similarity": 0.78,
            "uniqueness_margin": 0.08,
        },
        "budgets": {"maximum_frame_pixels": 8_000_000, "maximum_grounding_milliseconds": 2000},
    }


def test_policy_loads_from_checked_in_yaml() -> None:
    policy = load_vision_policy(Path("config/vision-policy.yaml"))

    assert isinstance(policy, VisionGroundingPolicy)
    assert policy.schema_version == "1.0"
    assert policy.image.canonical_width == 64


def test_policy_rejects_unknown_fields() -> None:
    payload = policy_payload()
    payload["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        VisionGroundingPolicy.model_validate(payload)


def test_policy_rejects_invalid_component_area_order() -> None:
    payload = policy_payload()
    payload["segmentation"] = {
        "minimum_component_area_ratio": 0.3,
        "maximum_component_area_ratio": 0.2,
        "maximum_components": 2000,
    }

    with pytest.raises(ValidationError, match="less than"):
        VisionGroundingPolicy.model_validate(payload)


@pytest.mark.parametrize("content", [b"", b"[]", b"schema_version: [broken"])
def test_policy_rejects_empty_non_mapping_or_malformed_yaml(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "vision-policy.yaml"
    path.write_bytes(content)

    with pytest.raises(ValueError, match="vision policy"):
        load_vision_policy(path)


def test_policy_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "vision-policy.yaml"
    path.write_bytes(b"x" * (64 * 1024 + 1))

    with pytest.raises(ValueError, match="64 KiB"):
        load_vision_policy(path)


def test_policy_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_vision_policy(tmp_path / "missing.yaml")
