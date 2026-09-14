from typing import Any

import pytest

from replayforge.capabilities.models import ObjectContract
from replayforge.capabilities.values import (
    ContractValidationError,
    binding_classification,
    contract_classifications,
    resolve_input,
    validate_object,
)
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.policy.types import DataClassification


def contract_with(property_schema: dict[str, Any]) -> ObjectContract:
    return ObjectContract.model_validate(
        {
            "required": ["value"],
            "properties": {
                "value": {
                    "description": "Test value",
                    "data_classification": "public",
                    **property_schema,
                }
            },
        }
    )


@pytest.mark.parametrize(
    ("contract", "value", "code"),
    [
        (contract_with({"type": "string", "min_length": 3}), "x", "too_short"),
        (contract_with({"type": "string", "max_length": 3}), "long", "too_long"),
        (contract_with({"type": "string", "pattern": "^[0-9]+$"}), "abc", "pattern_mismatch"),
        (contract_with({"type": "string", "format": "decimal"}), "money", "invalid_decimal"),
        (contract_with({"type": "string", "format": "decimal"}), "NaN", "invalid_decimal"),
        (contract_with({"type": "string", "format": "decimal"}), "Infinity", "invalid_decimal"),
        (contract_with({"type": "string", "format": "date"}), "not-date", "invalid_date"),
        (
            contract_with({"type": "string", "format": "date-time"}),
            "not-time",
            "invalid_datetime",
        ),
        (
            contract_with({"type": "string", "format": "date-time"}),
            "2026-09-10T12:30:00",
            "datetime_requires_timezone",
        ),
        (contract_with({"type": "integer"}), True, "expected_integer"),
        (contract_with({"type": "boolean"}), 1, "expected_boolean"),
        (contract_with({"type": "string", "enum": ["USD"]}), "EUR", "not_in_enum"),
        (contract_with({"type": "string", "const": "savings"}), "checking", "const_mismatch"),
    ],
)
def test_invalid_runtime_values_return_safe_codes(
    contract: ObjectContract, value: Any, code: str
) -> None:
    with pytest.raises(ContractValidationError) as error:
        validate_object(contract, {"value": value})

    assert error.value.path == "value"
    assert error.value.code == code


def test_object_contract_rejects_missing_and_unknown_properties() -> None:
    contract = contract_with({"type": "string"})

    with pytest.raises(ContractValidationError, match="missing_required"):
        validate_object(contract, {})
    with pytest.raises(ContractValidationError, match="unknown_properties"):
        validate_object(contract, {"value": "ok", "extra": "not allowed"})


def test_nested_object_is_validated_recursively() -> None:
    contract = contract_with(
        {
            "type": "object",
            "required": ["enabled"],
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Enabled flag",
                    "data_classification": "public",
                }
            },
        }
    )

    assert validate_object(contract, {"value": {"enabled": True}}) == {"value": {"enabled": True}}
    with pytest.raises(ContractValidationError, match="expected_object"):
        validate_object(contract, {"value": "not-an-object"})

    with pytest.raises(ContractValidationError) as error:
        validate_object(contract, {"value": {"enabled": "yes"}})
    assert error.value.path == "value.enabled"


def test_unknown_property_names_do_not_leak_into_errors() -> None:
    with pytest.raises(ContractValidationError) as error:
        validate_object(contract_with({"type": "string"}), {"value": "ok", "private-value": 1})
    assert "private-value" not in str(error.value)


def test_nested_input_resolution_and_missing_optional_values() -> None:
    assert resolve_input({"member": {"id": "12345"}}, "member.id") == "12345"
    missing_inputs: tuple[dict[str, Any], ...] = ({}, {"member": None}, {"member": {}})
    for inputs in missing_inputs:
        with pytest.raises(ContractValidationError, match="input_binding_missing"):
            resolve_input(inputs, "member.id")


def test_nested_personal_outputs_are_redacted_below_public_objects() -> None:
    contract = contract_with(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name", "data_classification": "personal"}
            },
        }
    )
    redacted = StructuredRedactor().sanitize_json(
        {"outputs": {"value": {"name": "Private Person"}}},
        contract_classifications(contract, "outputs"),
        run_salt="run",
    )
    assert b"Private Person" not in redacted.content
    assert "drop:outputs.value.name" in redacted.redaction_directives


@pytest.mark.parametrize("mode", ["remove", "tokenize", "last4"])
def test_explicit_redaction_also_protects_public_outputs(mode: str) -> None:
    contract = contract_with({"type": "string"})
    redacted = StructuredRedactor().sanitize_json(
        {"outputs": {"value": "private-value"}},
        contract_classifications(contract, "outputs", {"value": mode}),
        run_salt="run",
    )
    assert b"private-value" not in redacted.content


@pytest.mark.parametrize("mode", ["tokenize", "last4"])
def test_output_directives_cannot_weaken_personal_classification(mode: str) -> None:
    contract = contract_with({"type": "string", "data_classification": "personal"})
    classifications = contract_classifications(contract, "outputs", {"value": mode})
    assert classifications["outputs.value"] is DataClassification.PERSONAL


def test_forbidden_parent_classification_protects_public_nested_input() -> None:
    contract = contract_with(
        {
            "type": "object",
            "data_classification": "secret",
            "properties": {
                "token": {"type": "string", "description": "Token", "data_classification": "public"}
            },
        }
    )
    assert (
        binding_classification(contract, "value.token", frozenset({DataClassification.SECRET}))
        is DataClassification.SECRET
    )
    assert binding_classification(contract, "value.token", frozenset()) is DataClassification.PUBLIC
    assert binding_classification(contract, "missing", frozenset()) is None
