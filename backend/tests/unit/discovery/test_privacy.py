from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact, LocatorBundle
from replayforge.discovery.privacy import (
    extraction_locator_contains_value,
    validate_artifact_privacy,
)
from replayforge.evidence.redaction import EvidenceRejectedError


@pytest.mark.parametrize(
    "value", ["Synthetic Person", "12345", "synthetic@example.invalid", "123-45-6789"]
)
def test_artifact_metadata_cannot_retain_invocation_or_personal_shaped_values(
    valid_artifact_data: dict[str, Any], value: str
) -> None:
    valid_artifact_data["capability"]["description"] = f"Look up {value}."
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    with pytest.raises(EvidenceRejectedError):
        validate_artifact_privacy(artifact, {"query": value})


def test_invocation_examples_are_not_exempt_from_artifact_privacy(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    with pytest.raises(EvidenceRejectedError, match="literal invocation"):
        validate_artifact_privacy(artifact, {"nested": {"member_id": "12345"}})


def test_structural_artifact_without_invocation_literals_is_allowed(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["inputs"]["properties"]["member_id"].pop("example", None)
    validate_artifact_privacy(
        CapabilityArtifact.model_validate(valid_artifact_data), {"member_id": "12345"}
    )


@pytest.mark.parametrize("classification", ["financial", "personal"])
def test_artifact_cannot_embed_classified_captured_output(
    valid_artifact_data: dict[str, Any],
    classification: str,
) -> None:
    valid_artifact_data["capability"]["description"] = "Observed value: $7,832.25"
    valid_artifact_data["outputs"]["properties"]["available_balance"]["data_classification"] = (
        classification
    )
    with pytest.raises(EvidenceRejectedError, match="captured value"):
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(valid_artifact_data),
            {},
            outputs={"available_balance": "$7,832.25"},
        )


@pytest.mark.parametrize(
    "candidate",
    [
        {"strategy": "rendered_text", "value": "$7,832.25"},
        {"strategy": "ocr_text", "value": "$7,832.25"},
        {
            "strategy": "ocr_relative",
            "anchor": "Payoff amount",
            "target_text": "$7,832.25",
            "relation": "right_of",
        },
    ],
)
def test_extraction_locator_must_not_name_the_captured_value(candidate: dict[str, Any]) -> None:
    target = LocatorBundle.model_validate(
        {"description": "Amount", "visual_candidates": [candidate]}
    )
    assert extraction_locator_contains_value(target, "$7,832.25")


def test_structural_field_label_is_not_treated_as_an_output_value() -> None:
    target = LocatorBundle.model_validate(
        {
            "description": "Priority",
            "visual_candidates": [
                {"strategy": "rendered_field_value", "label": "High priority"},
            ],
        }
    )
    assert not extraction_locator_contains_value(target, "High")
    assert not extraction_locator_contains_value(target, "$7,832.25")
