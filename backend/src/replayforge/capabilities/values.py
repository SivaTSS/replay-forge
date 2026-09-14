"""Runtime validation for artifact invocation and extraction values."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from replayforge.capabilities.models import JsonValueType, ObjectContract, ValueSchema
from replayforge.policy.types import DataClassification


class ContractValidationError(ValueError):
    """A value does not satisfy an artifact contract."""

    def __init__(self, path: str, code: str) -> None:
        super().__init__(f"{path}: {code}")
        self.path = path
        self.code = code


def resolve_input(inputs: dict[str, Any], path: str) -> Any:
    """Resolve a declared dotted object path without echoing submitted values."""
    current: Any = inputs
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ContractValidationError(path, "input_binding_missing")
        current = current[part]
    return current


def contract_classifications(
    contract: ObjectContract, prefix: str
) -> dict[str, DataClassification]:
    """Preserve nested classifications when preparing a result for redaction."""
    result: dict[str, DataClassification] = {}

    def visit(schema: ValueSchema, path: str) -> None:
        result[path] = schema.data_classification
        for name, child in schema.properties.items():
            visit(child, f"{path}.{name}")

    for name, schema in contract.properties.items():
        visit(schema, f"{prefix}.{name}")
    return result


def binding_classification(
    contract: ObjectContract, path: str, forbidden: frozenset[DataClassification]
) -> DataClassification | None:
    """A forbidden parent classification also protects its nested fields."""
    properties = contract.properties
    classification = None
    for part in path.split("."):
        schema = properties.get(part)
        if schema is None:
            return None
        classification = schema.data_classification
        if classification in forbidden:
            return classification
        properties = schema.properties
    return classification


def validate_object(
    contract: ObjectContract, value: dict[str, Any], *, path: str = "$"
) -> dict[str, Any]:
    missing = set(contract.required) - value.keys()
    if missing:
        raise ContractValidationError(path, f"missing_required:{','.join(sorted(missing))}")
    unknown = value.keys() - contract.properties.keys()
    if unknown:
        raise ContractValidationError(path, "unknown_properties")
    return {
        key: _validate_value(
            contract.properties[key], item, key if path == "$" else f"{path}.{key}"
        )
        for key, item in value.items()
    }


def _validate_value(schema: ValueSchema, value: Any, path: str) -> Any:
    if schema.type is JsonValueType.STRING:
        if not isinstance(value, str):
            raise ContractValidationError(path, "expected_string")
        _validate_string(schema, value, path)
    elif schema.type is JsonValueType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ContractValidationError(path, "expected_integer")
    elif schema.type is JsonValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise ContractValidationError(path, "expected_boolean")
    elif schema.type is JsonValueType.OBJECT:
        if not isinstance(value, dict):
            raise ContractValidationError(path, "expected_object")
        nested = ObjectContract(
            required=schema.required,
            properties=schema.properties,
            additional_properties=False,
        )
        value = validate_object(nested, value, path=path)
    if schema.enum and value not in schema.enum:
        raise ContractValidationError(path, "not_in_enum")
    if schema.const is not None and value != schema.const:
        raise ContractValidationError(path, "const_mismatch")
    return value


def _validate_string(schema: ValueSchema, value: str, path: str) -> None:
    if schema.min_length is not None and len(value) < schema.min_length:
        raise ContractValidationError(path, "too_short")
    if schema.max_length is not None and len(value) > schema.max_length:
        raise ContractValidationError(path, "too_long")
    if schema.pattern is not None and re.fullmatch(schema.pattern, value) is None:
        raise ContractValidationError(path, "pattern_mismatch")
    if schema.format == "decimal":
        try:
            parsed_decimal = Decimal(value)
        except InvalidOperation as exc:
            raise ContractValidationError(path, "invalid_decimal") from exc
        if not parsed_decimal.is_finite():
            raise ContractValidationError(path, "invalid_decimal")
    elif schema.format == "date":
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ContractValidationError(path, "invalid_date") from exc
    elif schema.format == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractValidationError(path, "invalid_datetime") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ContractValidationError(path, "datetime_requires_timezone")
