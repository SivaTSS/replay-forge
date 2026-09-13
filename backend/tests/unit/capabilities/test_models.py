from typing import Any

import pytest
from pydantic import ValidationError

from replayforge.capabilities.models import (
    CapabilityArtifact,
    ExtractAction,
    ImageAnchorCandidate,
    LocatorCandidate,
    NormalizedRegion,
    OcrAnchor,
    RelativeRegion,
    RenderedFieldValueCandidate,
    RenderedGroupImageCandidate,
    RenderedLabeledControlCandidate,
    RenderedTextCandidate,
    RetryPolicy,
    ValueSchema,
)


def add_recovery(artifact: dict[str, Any]) -> dict[str, Any]:
    recovery = {
        "id": "dismiss.notice",
        "trigger": {"kind": "text", "value": "Important notice", "match": "exact"},
        "max_uses": 1,
        "steps": [
            {
                "id": "recovery.dismiss_notice",
                "name": "Dismiss notice",
                "action": {"kind": "click"},
                "target": {
                    "description": "Continue notice",
                    "candidates": [{"strategy": "role_name", "role": "link", "name": "Continue"}],
                },
                "risk": "read_only",
            }
        ],
        "resume_at": "account.extract_balance",
    }
    artifact["recoveries"] = [recovery]
    artifact["steps"][1]["recovery_refs"] = [recovery["id"]]
    return recovery


def add_application_failure(artifact: dict[str, Any]) -> dict[str, Any]:
    failure = {
        "code": "permission_denied",
        "description": "The current role cannot view the requested member.",
        "detect": {"kind": "text", "value": "Permission denied", "match": "exact"},
        "allowed_after_steps": ["search.submit"],
        "expected_state": "member_results",
        "observed_state": "permission_denied",
        "recoverable": False,
    }
    artifact["failures"] = [failure]
    artifact["steps"][1]["failure_refs"] = [failure["code"]]
    return failure


def test_lowercase_is_an_explicit_extraction_transform(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["steps"][2]["action"]["transform"] = "lowercase"

    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    action = artifact.steps[2].action
    assert isinstance(action, ExtractAction)
    assert action.transform == "lowercase"


def test_valid_artifact_is_deeply_immutable(valid_artifact_data: dict[str, Any]) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    with pytest.raises(ValidationError, match="frozen"):
        artifact.capability.version = "1.0.1"


def test_unknown_fields_are_rejected(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["provider_transcript"] = {"reasoning": "must not be persisted"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_unknown_input_reference_is_rejected(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][0]["action"]["value"]["path"] = "account_number"

    with pytest.raises(ValidationError, match="references an unknown input"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_duplicate_step_ids_are_rejected(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][1]["id"] = valid_artifact_data["steps"][0]["id"]

    with pytest.raises(ValidationError, match="step IDs must be unique"):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_recovery", "recovery IDs must be unique"),
        ("duplicate_step", "main and recovery step IDs must be unique"),
        ("disallowed_action", "uses a disallowed action type"),
        ("unknown_select_input", "references an unknown input"),
        ("nested_recovery", "cannot invoke a nested recovery"),
        ("sensitive_recovery", "cannot require human approval"),
    ],
)
def test_recovery_steps_obey_artifact_safety_invariants(
    valid_artifact_data: dict[str, Any], mutation: str, message: str
) -> None:
    recovery = add_recovery(valid_artifact_data)
    recovery_step = recovery["steps"][0]
    if mutation == "duplicate_recovery":
        valid_artifact_data["recoveries"].append(dict(recovery))
    elif mutation == "duplicate_step":
        recovery_step["id"] = "search.submit"
    elif mutation == "disallowed_action":
        recovery_step["action"] = {
            "kind": "select",
            "option": {"source": "literal", "value": "Normal"},
        }
    elif mutation == "unknown_select_input":
        valid_artifact_data["policy"]["allowed_action_types"].append("select")
        recovery_step["action"] = {
            "kind": "select",
            "option": {"source": "input", "path": "missing"},
        }
    elif mutation == "sensitive_recovery":
        valid_artifact_data["capability"]["risk"] = "sensitive"
        valid_artifact_data["policy"]["maximum_risk"] = "sensitive"
        recovery_step["risk"] = "sensitive"
    else:
        recovery_step["recovery_refs"] = ["dismiss.notice"]

    with pytest.raises(ValidationError, match=message):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate", "application failure codes must be unique"),
        ("outcome_collision", "codes must be distinct"),
        ("unknown_step", "references an unknown step"),
        ("disallowed_step", "is not allowed after step"),
    ],
)
def test_application_failure_declarations_are_consistent(
    valid_artifact_data: dict[str, Any], mutation: str, message: str
) -> None:
    failure = add_application_failure(valid_artifact_data)
    if mutation == "duplicate":
        valid_artifact_data["failures"].append(dict(failure))
    elif mutation == "outcome_collision":
        failure["code"] = "member_not_found"
        valid_artifact_data["steps"][1]["failure_refs"] = ["member_not_found"]
    elif mutation == "unknown_step":
        failure["allowed_after_steps"] = ["missing.step"]
    else:
        failure["allowed_after_steps"] = ["search.enter_member_id"]

    with pytest.raises(ValidationError, match=message):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_required_output_must_be_checked_at_checkpoint(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["checkpoint"]["condition"] = {
        "kind": "route",
        "pattern": "/accounts/*/details",
    }

    with pytest.raises(ValidationError, match="checkpoint does not validate required outputs"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_step_cannot_exceed_capability_risk(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][1]["risk"] = "irreversible"

    with pytest.raises(ValidationError, match="exceeds the capability risk ceiling"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_coordinate_locator_requires_dimensions_and_low_portability(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["steps"][0]["target"]["candidates"] = [
        {"strategy": "coordinates", "x": 10, "y": 20}
    ]

    with pytest.raises(ValidationError, match="coordinate locator requires"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_schema_forbids_additional_top_level_properties() -> None:
    schema = CapabilityArtifact.model_json_schema()

    assert schema["additionalProperties"] is False


def test_contextual_image_anchor_requires_both_context_fields() -> None:
    base = {
        "strategy": "image_anchor",
        "asset_key": "asset://sha256/" + "a" * 64,
        "content_hash": "sha256:" + "a" * 64,
    }
    with pytest.raises(ValidationError, match="provided together"):
        ImageAnchorCandidate.model_validate({**base, "context_anchor": {"value": "Savings"}})
    with pytest.raises(ValidationError, match="provided together"):
        ImageAnchorCandidate.model_validate(
            {**base, "relative_search_region": {"x": 1, "y": 0, "width": 4, "height": 2}}
        )


def test_contextual_image_anchor_cannot_have_two_search_regions() -> None:
    with pytest.raises(ValidationError, match="top-level search_region"):
        ImageAnchorCandidate(
            strategy="image_anchor",
            asset_key="asset://sha256/" + "a" * 64,
            content_hash="sha256:" + "a" * 64,
            search_region=NormalizedRegion(x=0, y=0, width=1, height=1),
            context_anchor=OcrAnchor(value="Savings"),
            relative_search_region=RelativeRegion(x=1, y=0, width=4, height=2),
        )


@pytest.mark.parametrize(
    "candidate",
    [
        RenderedTextCandidate(strategy="rendered_text", value="Search"),
        RenderedLabeledControlCandidate(
            strategy="rendered_labeled_control",
            label="Member ID",
            control_kind="text_input",
        ),
        RenderedFieldValueCandidate(strategy="rendered_field_value", label="Currency"),
        RenderedGroupImageCandidate(
            strategy="rendered_group_image",
            group_label="Savings",
            asset_key="asset://sha256/" + "a" * 64,
            content_hash="sha256:" + "a" * 64,
        ),
    ],
)
def test_geometry_free_candidates_accept_semantic_identity(candidate: object) -> None:
    assert candidate


def test_geometry_free_candidate_rejects_layout_fields() -> None:
    with pytest.raises(ValidationError):
        RenderedTextCandidate.model_validate(
            {"strategy": "rendered_text", "value": "Search", "x": 10}
        )


def test_schema_one_point_three_rejects_legacy_target(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["schema_version"] = "1.3"
    with pytest.raises(ValidationError, match="cannot contain DOM"):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            {
                "type": "object",
                "description": "Nested value",
                "data_classification": "public",
                "required": ["missing"],
                "properties": {},
            },
            "required properties are not declared",
        ),
        (
            {
                "type": "string",
                "description": "Scalar value",
                "data_classification": "public",
                "required": ["invalid"],
            },
            "only object schemas",
        ),
        (
            {
                "type": "string",
                "description": "Bad bounds",
                "data_classification": "public",
                "min_length": 10,
                "max_length": 2,
            },
            "min_length cannot exceed",
        ),
        (
            {
                "type": "string",
                "description": "Bad expression",
                "data_classification": "public",
                "pattern": "[",
            },
            "pattern must be a valid regular expression",
        ),
    ],
)
def test_value_schema_rejects_invalid_shapes(schema: dict[str, Any], message: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        ValueSchema.model_validate(schema)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            {
                "type": "integer",
                "description": "Contradictory integer",
                "data_classification": "public",
                "pattern": "^[0-9]+$",
            },
            "only string schemas",
        ),
        (
            {
                "type": "boolean",
                "description": "Wrong constant type",
                "data_classification": "public",
                "const": "true",
            },
            "const must match",
        ),
        (
            {
                "type": "integer",
                "description": "Wrong enum type",
                "data_classification": "public",
                "enum": [1, "2"],
            },
            "enum values must match",
        ),
        (
            {
                "type": "string",
                "description": "Conflicting constant",
                "data_classification": "public",
                "enum": ["open", "closed"],
                "const": "missing",
            },
            "const must be included",
        ),
        (
            {
                "type": "object",
                "description": "Object with scalar constraint",
                "data_classification": "public",
                "format": "date",
            },
            "object schemas cannot declare scalar",
        ),
    ],
)
def test_value_schema_rejects_constraints_for_a_different_type(
    schema: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ValueSchema.model_validate(schema)


def test_integer_value_schema_accepts_integer_enum() -> None:
    schema = ValueSchema.model_validate(
        {
            "type": "integer",
            "description": "Retry count",
            "data_classification": "operational",
            "enum": [1, 2, 3],
        }
    )

    assert schema.enum == (1, 2, 3)


def test_object_contract_rejects_duplicate_and_invalid_field_names(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["inputs"]["required"] = ["member_id", "member_id"]
    with pytest.raises(ValidationError, match="required properties must be unique"):
        CapabilityArtifact.model_validate(valid_artifact_data)

    valid_artifact_data["inputs"]["required"] = ["member_id"]
    valid_artifact_data["inputs"]["properties"]["Member ID"] = valid_artifact_data["inputs"][
        "properties"
    ]["member_id"]
    with pytest.raises(ValidationError, match="property names are invalid"):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ({"strategy": "role_name", "role": "button"}, "requires role and name"),
        ({"strategy": "label"}, "label locator requires value"),
        (
            {"strategy": "relative_text", "anchor": "Balance"},
            "requires anchor, relation, and element",
        ),
        (
            {
                "strategy": "coordinates",
                "x": 1,
                "y": 2,
                "width": 10,
                "height": 10,
                "viewport_width": 100,
                "viewport_height": 100,
            },
            "must declare low portability",
        ),
    ],
)
def test_locator_candidate_rejects_incomplete_strategy_data(
    candidate: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        LocatorCandidate.model_validate(candidate)


def test_locator_candidate_rejects_fields_from_another_strategy() -> None:
    with pytest.raises(ValidationError, match="contains unrelated fields"):
        LocatorCandidate.model_validate(
            {
                "strategy": "role_name",
                "role": "button",
                "name": "Submit",
                "value": "#submit",
            }
        )


@pytest.mark.parametrize(
    "retry",
    [
        {"max_attempts": 1, "backoff_ms": [100]},
        {"max_attempts": 2, "backoff_ms": [30_001]},
        {"max_attempts": 2},
        {"retry_on": ["temporary", "temporary"]},
        {"retry_on": ["NOT_STABLE"]},
        {"max_attempts": 2, "retry_on": ["temporary"], "require_effect_absent": False},
    ],
)
def test_retry_policy_is_strictly_bounded(retry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate(retry)


def test_targeted_action_requires_locator(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][1].pop("target")

    with pytest.raises(ValidationError, match="click action requires a target"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_non_targeted_action_rejects_locator(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][0]["action"] = {
        "kind": "assert",
        "condition": {"kind": "route", "pattern": "/members/search"},
    }

    with pytest.raises(ValidationError, match="assert action cannot declare a target"):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("condition_output", "condition references unknown output"),
        ("identity_input", "identity condition references unknown input"),
        ("outcome_input", "outcome member_not_found references an unknown input"),
        ("checkpoint_action", "references an unknown checkpoint"),
        ("duplicate_step_ref", "outcome references must be unique"),
    ],
)
def test_artifact_rejects_dangling_or_duplicate_semantic_references(
    valid_artifact_data: dict[str, Any], mutation: str, message: str
) -> None:
    if mutation == "condition_output":
        valid_artifact_data["steps"][0]["postconditions"] = [
            {"kind": "output_valid", "output": "missing"}
        ]
    elif mutation == "identity_input":
        valid_artifact_data["checkpoint"]["condition"]["conditions"].append(
            {
                "kind": "identity_matches",
                "extracted_output": "available_balance",
                "input_path": "missing",
            }
        )
    elif mutation == "outcome_input":
        valid_artifact_data["outcomes"][0]["result"]["details"]["member_id"]["path"] = "missing"
    elif mutation == "checkpoint_action":
        valid_artifact_data["steps"][0].pop("target")
        valid_artifact_data["steps"][0]["action"] = {
            "kind": "checkpoint",
            "checkpoint_id": "missing",
        }
        valid_artifact_data["policy"]["allowed_action_types"].append("checkpoint")
    else:
        valid_artifact_data["steps"][1]["outcome_refs"] = [
            "member_not_found",
            "member_not_found",
        ]

    with pytest.raises(ValidationError, match=message):
        CapabilityArtifact.model_validate(valid_artifact_data)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("compatibility_family", "application families must match"),
        ("policy_risk", "risk and policy maximum risk must match"),
        ("compatibility_entry", "entry point is not allowed"),
        ("disallowed_action", "uses a disallowed action type"),
        ("unknown_recovery", "references an unknown recovery"),
        ("unknown_outcome", "references an unknown business outcome"),
        ("unknown_failure", "references an unknown application failure"),
    ],
)
def test_artifact_rejects_cross_reference_and_policy_conflicts(
    valid_artifact_data: dict[str, Any], mutation: str, message: str
) -> None:
    if mutation == "compatibility_family":
        valid_artifact_data["compatibility"]["application_family"] = "other_app"
    elif mutation == "policy_risk":
        valid_artifact_data["policy"]["maximum_risk"] = "reversible"
    elif mutation == "compatibility_entry":
        valid_artifact_data["compatibility"]["entry_point"] = "account_admin"
    elif mutation == "disallowed_action":
        valid_artifact_data["policy"]["allowed_action_types"].remove("click")
    elif mutation == "unknown_recovery":
        valid_artifact_data["steps"][0]["recovery_refs"] = ["missing"]
    elif mutation == "unknown_outcome":
        valid_artifact_data["steps"][0]["outcome_refs"] = ["missing"]
    else:
        valid_artifact_data["steps"][0]["failure_refs"] = ["missing"]

    with pytest.raises(ValidationError, match=message):
        CapabilityArtifact.model_validate(valid_artifact_data)
