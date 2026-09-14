from copy import deepcopy
from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact, LocatorBundle
from replayforge.discovery.privacy import (
    ArtifactPrivacyError,
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


def test_privacy_diagnostic_reports_only_schema_location(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["capability"]["description"] = "Look up Private Person."
    with pytest.raises(ArtifactPrivacyError) as error:
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(valid_artifact_data), {"query": "Private Person"}
        )
    assert error.value.source == "invocation"
    assert error.value.location == "artifact.capability.description"
    assert "Private Person" not in str(error.value)


def test_privacy_diagnostic_does_not_echo_untrusted_dictionary_keys(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["inputs"]["properties"]["private_identifier"] = valid_artifact_data[
        "inputs"
    ]["properties"]["member_id"].copy()
    with pytest.raises(ArtifactPrivacyError) as error:
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(valid_artifact_data), {"query": "private_identifier"}
        )
    assert error.value.location == "artifact.inputs.properties.*:key"
    assert "private_identifier" not in str(error.value)


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


def _with_status_contract(data: dict[str, Any]) -> CapabilityArtifact:
    data["outputs"]["required"] += ["posted_date", "posting_status"]
    for name in ("posted_date", "posting_status"):
        data["outputs"]["properties"][name] = {
            "type": "string",
            "description": "Value read from the selected record",
            "data_classification": "personal",
            "persistence": "redacted",
        }
    for index, name in enumerate(("posted_date", "posting_status")):
        step = deepcopy(data["steps"][2])
        step["id"] = f"record.extract_{index}"
        step["action"]["output"] = name
        step["postconditions"] = [{"kind": "output_valid", "output": name}]
        data["steps"].append(step)
    data["checkpoint"]["condition"] = {
        "kind": "all",
        "conditions": [
            data["checkpoint"]["condition"],
            {"kind": "output_valid", "output": "posted_date"},
            {"kind": "output_valid", "output": "posting_status"},
        ],
    }
    return CapabilityArtifact.model_validate(data)


def test_captured_status_does_not_match_partial_contract_symbol(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = _with_status_contract(valid_artifact_data)
    validate_artifact_privacy(artifact, {}, outputs={"posting_status": "Posted"})


@pytest.mark.parametrize("value", ["posted_date", "posting_status"])
def test_captured_value_cannot_be_copied_as_a_complete_contract_symbol(
    valid_artifact_data: dict[str, Any], value: str
) -> None:
    artifact = _with_status_contract(valid_artifact_data)
    with pytest.raises(ArtifactPrivacyError):
        validate_artifact_privacy(artifact, {}, outputs={"posting_status": value})


def test_invocation_substrings_remain_forbidden_in_contract_symbols(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = _with_status_contract(valid_artifact_data)
    with pytest.raises(ArtifactPrivacyError):
        validate_artifact_privacy(artifact, {"query": "Posted"})


@pytest.mark.parametrize("field", ["description", "example", "const", "enum", "pattern"])
def test_contract_symbol_handling_does_not_exempt_schema_literal_content(
    valid_artifact_data: dict[str, Any], field: str
) -> None:
    artifact = _with_status_contract(valid_artifact_data)
    data = artifact.model_dump(mode="json")
    data["outputs"]["properties"]["posted_date"][field] = (
        ["Posted"] if field == "enum" else "Posted"
    )
    with pytest.raises(ArtifactPrivacyError):
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(data), {}, outputs={"posting_status": "Posted"}
        )


def test_symbol_name_in_free_text_is_not_exempt(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = _with_status_contract(valid_artifact_data)
    data = artifact.model_dump(mode="json")
    data["capability"]["description"] = "Return posted_date."
    with pytest.raises(ArtifactPrivacyError):
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(data), {}, outputs={"posting_status": "Posted"}
        )


def test_known_string_cannot_be_embedded_as_numeric_example(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["inputs"]["properties"]["member_id"] = {
        "type": "integer",
        "description": "Record identifier",
        "data_classification": "customer_identifier",
        "example": 12345,
    }
    with pytest.raises(ArtifactPrivacyError):
        validate_artifact_privacy(
            CapabilityArtifact.model_validate(valid_artifact_data), {"query": "12345"}
        )
