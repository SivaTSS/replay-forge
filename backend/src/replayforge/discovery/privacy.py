"""Reject known invocation and classified output data before artifact publication.

This is a deterministic leak guard, not semantic PII detection: unknown personal
values from a target still require a deployment-specific data policy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel

from replayforge.capabilities.models import (
    CapabilityArtifact,
    ExtractAction,
    IdentityMatchesCondition,
    InputValue,
    LocatorBundle,
    LocatorStrategy,
    ObjectContract,
    OutputEqualsCondition,
    OutputValidCondition,
    RenderedFieldValueCandidate,
    ValueSchema,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.policy.types import DataClassification

_PERSONAL_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


class ArtifactPrivacyError(EvidenceRejectedError):
    """A known-data collision, with a schema-only location and no rejected value."""

    def __init__(self, source: Literal["invocation", "captured"], location: str) -> None:
        self.source = source
        self.location = location
        super().__init__(f"artifact contains a literal {source} value at {location}")


_REFERENCE_FIELDS: dict[type[BaseModel], frozenset[str]] = {
    InputValue: frozenset({"path"}),
    ExtractAction: frozenset({"output"}),
    OutputValidCondition: frozenset({"output"}),
    OutputEqualsCondition: frozenset({"output"}),
    IdentityMatchesCondition: frozenset({"extracted_output", "input_path"}),
}


def _match_location(
    value: Any,
    serialized: str,
    path: str = "artifact",
    *,
    source: Literal["invocation", "captured"] = "invocation",
    reference: bool = False,
) -> str | None:
    """Paths contain model field names/indices; caller-controlled dictionary keys are opaque."""
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            if path == "artifact" and name == "provenance":
                continue
            # Static Python schema keys cannot have been copied from captured UI data.
            if source == "invocation" and serialized in name.casefold():
                return f"{path}.{name}:key"
            is_contract = isinstance(value, ObjectContract | ValueSchema)
            is_reference = name in _REFERENCE_FIELDS.get(type(value), frozenset()) or (
                is_contract and name in {"properties", "required"}
            )
            found = _match_location(
                getattr(value, name),
                serialized,
                f"{path}.{name}",
                source=source,
                reference=is_reference,
            )
            if found is not None:
                return found
    elif isinstance(value, dict):
        for key, item in value.items():
            encoded_key = json.dumps(str(key), ensure_ascii=False)[1:-1].casefold()
            if _matches_literal(encoded_key, serialized, source, reference):
                return f"{path}.*:key"
            # Only contract property keys are symbols. Their schemas remain fully scanned.
            found = _match_location(item, serialized, f"{path}.*", source=source)
            if found is not None:
                return found
    elif isinstance(value, list | tuple | set | frozenset):
        for index, item in enumerate(value):
            found = _match_location(
                item, serialized, f"{path}[{index}]", source=source, reference=reference
            )
            if found is not None:
                return found
    elif (
        isinstance(value, str)
        and _matches_literal(
            json.dumps(value, ensure_ascii=False)[1:-1].casefold(),
            serialized,
            source,
            reference,
        )
    ) or (isinstance(value, int | float | bool) and serialized in json.dumps(value).casefold()):
        return path
    return None


def _matches_literal(
    encoded: str,
    serialized: str,
    source: Literal["invocation", "captured"],
    reference: bool,
) -> bool:
    # A bound schema identifier is not free text: a captured status must not match
    # a substring of an output name. Whole-symbol copies still fail. Invocation
    # values remain substring-checked even in symbols, as they predate the draft.
    if source == "captured" and reference:
        return encoded == serialized
    return serialized in encoded


def target_contains_invocation_literal(target: LocatorBundle, inputs: dict[str, Any]) -> bool:
    """Known invocation strings cannot become a selector or target description."""
    content = target.model_dump_json().casefold()

    def contains(value: object) -> bool:
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return (
            isinstance(value, str)
            and len(value.strip()) >= 4
            and json.dumps(value, ensure_ascii=False)[1:-1].casefold() in content
        )

    return contains(inputs)


def validate_artifact_privacy(
    artifact: CapabilityArtifact,
    inputs: dict[str, Any],
    redactor: StructuredRedactor | None = None,
    outputs: dict[str, Any] | None = None,
) -> None:
    full_content = artifact.model_dump_json()
    (redactor or StructuredRedactor()).validate_text(full_content)
    if any(pattern.search(full_content) for pattern in _PERSONAL_PATTERNS):
        raise EvidenceRejectedError("artifact contains personal-data-shaped text")

    for source, value in _known_values(artifact, inputs, outputs):
        # Match serialized text too: quotes/newlines must not evade the guard.
        serialized = json.dumps(value, ensure_ascii=False)[1:-1].casefold()
        location = _match_location(artifact, serialized, source=source)
        if location is not None:
            raise ArtifactPrivacyError(source, location)


def _known_values(
    artifact: CapabilityArtifact, inputs: dict[str, Any], outputs: dict[str, Any] | None
) -> Iterator[tuple[Literal["invocation", "captured"], str]]:
    def strings(value: object) -> Iterator[str]:
        if isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, str) and len(value.strip()) >= 4:
            yield value

    for value in strings(inputs):
        yield "invocation", value
    for name, value in (outputs or {}).items():
        schema = artifact.outputs.properties.get(name)
        if schema is not None and schema.data_classification in {
            DataClassification.PERSONAL,
            DataClassification.CUSTOMER_IDENTIFIER,
            DataClassification.FINANCIAL,
        }:
            for item in strings(value):
                yield "captured", item


def redact_contract_descriptions(
    artifact: CapabilityArtifact,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    redactor: StructuredRedactor | None = None,
) -> CapabilityArtifact:
    """Redact documentation only; never rewrite executable or policy-relevant content.

    A whole field is removed on a known-value match, avoiding partial identifiers or
    Unicode replacement errors. Publication must still run the complete privacy guard.
    """
    (redactor or StructuredRedactor()).validate_text(artifact.model_dump_json())
    private = tuple(value.casefold() for _, value in _known_values(artifact, inputs, outputs))

    def clean(schema: ValueSchema) -> ValueSchema:
        description = schema.description
        if any(value in description.casefold() for value in private):
            description = "[REDACTED]"
        return schema.model_copy(
            update={
                "description": description,
                "properties": {name: clean(child) for name, child in schema.properties.items()},
            }
        )

    def contract(value: ObjectContract) -> ObjectContract:
        return value.model_copy(
            update={
                "properties": {name: clean(schema) for name, schema in value.properties.items()}
            }
        )

    cleaned = artifact.model_copy(
        update={"inputs": contract(artifact.inputs), "outputs": contract(artifact.outputs)}
    )
    if cleaned == artifact:
        return artifact
    return cleaned.model_copy(
        update={
            "provenance": cleaned.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(cleaned)}
            )
        }
    )


def extraction_locator_contains_value(target: LocatorBundle | None, value: str) -> bool:
    """Detect locators coupled to the value they just extracted, not a stable field."""
    value = value.strip().casefold()
    if target is None or len(value) < 4:
        return False

    for candidate in (*target.visual_candidates, *target.candidates):
        if isinstance(candidate, RenderedFieldValueCandidate) or (
            candidate.strategy == LocatorStrategy.RELATIVE_TEXT
        ):
            continue
        fields = candidate.model_dump(mode="json")
        if any(
            isinstance(fields.get(name), str) and value in fields[name].casefold()
            for name in ("value", "text", "name", "anchor", "label", "target_text", "group_label")
        ):
            return True
    return False
