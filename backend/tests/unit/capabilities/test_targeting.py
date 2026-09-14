from typing import Any

import pytest

from replayforge.capabilities.models import (
    CapabilityArtifact,
    InputTextCandidate,
    InputValue,
    JsonValueType,
    LocatorBundle,
    ObjectContract,
    OcrRelativeCandidate,
    RenderedTextCandidate,
    ValueSchema,
)
from replayforge.capabilities.targeting import bind_target_inputs
from replayforge.capabilities.values import ContractValidationError
from replayforge.policy.types import DataClassification

FORBIDDEN = frozenset({DataClassification.CREDENTIAL, DataClassification.SECRET})


def contract(classification: str = "personal") -> ObjectContract:
    return ObjectContract.model_validate(
        {
            "properties": {
                "record": {
                    "type": "string",
                    "description": "Record identity",
                    "data_classification": classification,
                }
            },
            "required": ["record"],
        }
    )


def target(relative: bool = False, path: str = "record") -> LocatorBundle:
    return LocatorBundle(
        description="Requested record control",
        visual_candidates=(
            InputTextCandidate(
                strategy="input_text",
                value=InputValue(source="input", path=path),
                target_text="Open" if relative else None,
                relation="same_row" if relative else None,
            ),
        ),
    )


@pytest.mark.parametrize("identity", ["PART-82", "注文-七", "a.*[b]", "O'Reilly", "42"])
@pytest.mark.parametrize("relative", [False, True])
def test_identity_binding_is_exact_transient_text_not_code(identity: str, relative: bool) -> None:
    symbolic = target(relative)
    bound = bind_target_inputs(symbolic, {"record": identity}, contract(), FORBIDDEN)
    candidate = bound.visual_candidates[0]
    if relative:
        assert isinstance(candidate, OcrRelativeCandidate)
        assert candidate.anchor == identity
        assert candidate.anchor_match == "exact"
        assert candidate.target_text == "Open"
    else:
        assert isinstance(candidate, RenderedTextCandidate)
        assert candidate.value == identity
        assert candidate.match == "exact"
    assert identity not in symbolic.model_dump_json()
    assert isinstance(symbolic.visual_candidates[0], InputTextCandidate)


@pytest.mark.parametrize("value", [None, 42, True, {}, [], "", " " * 3, "a" * 201])
def test_unusable_identity_fails_without_echoing_values(value: Any) -> None:
    with pytest.raises(ContractValidationError, match="target_input_invalid"):
        bind_target_inputs(target(), {"record": value}, contract(), FORBIDDEN)


def test_missing_and_forbidden_inputs_fail_before_grounding() -> None:
    with pytest.raises(ContractValidationError, match="input_binding_missing"):
        bind_target_inputs(target(), {}, contract(), FORBIDDEN)
    with pytest.raises(ContractValidationError, match="target_input_forbidden"):
        bind_target_inputs(target(), {"record": "private"}, contract("secret"), FORBIDDEN)
    with pytest.raises(ContractValidationError, match="target_input_forbidden"):
        bind_target_inputs(target(), {"record": "private"}, None, FORBIDDEN)


def test_forbidden_parent_protects_nested_identity() -> None:
    nested = ObjectContract(
        required=("record",),
        properties={
            "record": ValueSchema(
                type=JsonValueType.OBJECT,
                description="Protected object",
                data_classification=DataClassification.SECRET,
                properties={"code": contract().properties["record"]},
            )
        },
    )
    with pytest.raises(ContractValidationError, match="target_input_forbidden"):
        bind_target_inputs(
            target(path="record.code"), {"record": {"code": "PART-82"}}, nested, FORBIDDEN
        )


def test_artifact_rejects_an_undeclared_target_binding(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["steps"][0]["target"] = target(path="undeclared").model_dump()
    with pytest.raises(ValueError, match="target references an unknown input"):
        CapabilityArtifact.model_validate(valid_artifact_data)
