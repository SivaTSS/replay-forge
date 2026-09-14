"""Reject known invocation and classified output data before artifact publication.

This is a deterministic leak guard, not semantic PII detection: unknown personal
values from a target still require a deployment-specific data policy.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel

from replayforge.capabilities.models import (
    CapabilityArtifact,
    LocatorBundle,
    LocatorStrategy,
    RenderedFieldValueCandidate,
)
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


def _match_location(value: Any, serialized: str, path: str = "artifact") -> str | None:
    """Paths contain model field names/indices; caller-controlled dictionary keys are opaque."""
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            if path == "artifact" and name == "provenance":
                continue
            if serialized in json.dumps(name, ensure_ascii=False)[1:-1].casefold():
                return f"{path}.{name}:key"
            found = _match_location(getattr(value, name), serialized, f"{path}.{name}")
            if found is not None:
                return found
    elif isinstance(value, dict):
        for key, item in value.items():
            if serialized in json.dumps(str(key), ensure_ascii=False)[1:-1].casefold():
                return f"{path}.*:key"
            found = _match_location(item, serialized, f"{path}.*")
            if found is not None:
                return found
    elif isinstance(value, list | tuple | set | frozenset):
        for index, item in enumerate(value):
            found = _match_location(item, serialized, f"{path}[{index}]")
            if found is not None:
                return found
    elif (
        isinstance(value, str)
        and serialized in json.dumps(value, ensure_ascii=False)[1:-1].casefold()
    ):
        return path
    return None


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
    # Provenance is runtime-generated; a random run ID may coincidentally contain
    # a short numeric input. It is not an invocation value embedded by the model.
    content = artifact.model_dump_json(exclude={"provenance"}).casefold()

    def check(value: object, source: Literal["invocation", "captured"]) -> None:
        if isinstance(value, dict):
            for item in value.values():
                check(item, source)
        elif isinstance(value, list):
            for item in value:
                check(item, source)
        elif isinstance(value, str) and len(value.strip()) >= 4:
            # Match serialized text too: quotes/newlines must not evade the guard.
            serialized = json.dumps(value, ensure_ascii=False)[1:-1].casefold()
            if serialized in content:
                location = _match_location(artifact, serialized) or "artifact"
                raise ArtifactPrivacyError(source, location)

    check(inputs, "invocation")
    for name, value in (outputs or {}).items():
        schema = artifact.outputs.properties.get(name)
        if schema is not None and schema.data_classification in {
            DataClassification.PERSONAL,
            DataClassification.CUSTOMER_IDENTIFIER,
            DataClassification.FINANCIAL,
        }:
            check(value, "captured")


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
