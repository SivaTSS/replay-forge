"""Reject known invocation data in an artifact before it can be published.

This is a deterministic leak guard, not semantic PII detection: unknown personal
values from a target still require a deployment-specific data policy.
"""

from __future__ import annotations

import json
import re
from typing import Any

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor

_PERSONAL_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


def validate_artifact_privacy(
    artifact: CapabilityArtifact,
    inputs: dict[str, Any],
    redactor: StructuredRedactor | None = None,
) -> None:
    full_content = artifact.model_dump_json()
    (redactor or StructuredRedactor()).validate_text(full_content)
    if any(pattern.search(full_content) for pattern in _PERSONAL_PATTERNS):
        raise EvidenceRejectedError("artifact contains personal-data-shaped text")
    # Provenance is runtime-generated; a random run ID may coincidentally contain
    # a short numeric input. It is not an invocation value embedded by the model.
    content = artifact.model_dump_json(exclude={"provenance"}).casefold()

    def check(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str) and len(value.strip()) >= 4:
            # Match serialized text too: quotes/newlines must not evade the guard.
            serialized = json.dumps(value, ensure_ascii=False)[1:-1].casefold()
            if serialized in content:
                raise EvidenceRejectedError("artifact contains a literal invocation value")

    check(inputs)
