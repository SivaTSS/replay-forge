from typing import Any

import pytest

from replayforge.capabilities.models import ObjectContract
from replayforge.capabilities.values import ContractValidationError, resolve_input, validate_object


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
