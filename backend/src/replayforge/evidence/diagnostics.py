"""Value-free execution diagnostics, independent of application labels and data."""

from __future__ import annotations

import io
import zipfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from replayforge.evidence.models import SanitizedEvidence
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.policy.types import DataClassification
from replayforge.shared.ids import EntityKind, parse_id

DiagnosticPhase = Literal["before_dispatch", "after_dispatch", "completion", "unknown"]
_CODES = frozenset(
    {
        "target_absent",
        "target_ambiguous",
        "action_timeout",
        "action_failed",
        "action_condition_mismatch",
        "step_precondition_mismatch",
        "postcondition_mismatch",
        "checkpoint_mismatch",
        "policy_blocked",
        "invalid_input",
        "incompatible_tenant",
        "sensitive_action_requires_approval",
        "unexpected_dialog",
        "control_lease_expired",
        "control_lease_conflict",
        "resume_checkpoint_missing",
        "other",
    }
)
_ACTIONS = frozenset(
    {
        "click",
        "type",
        "select",
        "extract",
        "assert",
        "wait_for",
        "checkpoint",
        "navigate",
        "switch_context",
        "press_keys",
        "scroll",
        "unknown",
    }
)
_CONDITIONS = frozenset(
    {
        "route",
        "text",
        "visual_text",
        "rendered_text",
        "input_text",
        "element",
        "output_valid",
        "output_equals",
        "identity_matches",
        "all",
        "any",
        "not",
        "unknown",
    }
)


class ExecutionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    phase: DiagnosticPhase = "unknown"
    code: str
    action_type: str = "unknown"
    step_ordinal: int | None = Field(default=None, ge=1, le=10_000)
    expected_condition_kind: str = "unknown"
    expected_count: int | None = Field(default=None, ge=0, le=1_000_000)
    observed_count: int | None = Field(default=None, ge=0, le=1_000_000)
    attempt: int = Field(default=1, ge=1, le=5)
    max_attempts: int = Field(default=1, ge=1, le=5)
    retry_error_allowed: bool = False
    recovery_checked: bool = False
    dispatch_state: Literal["not_attempted", "attempted", "unknown"] = "unknown"
    effect_absent: bool | None = None

    def safe_payload(self) -> dict[str, object]:
        """Never retain open-ended codes, actions or condition names supplied by adapters."""
        payload = self.model_dump(exclude_none=True)
        for key, allowed in (
            ("code", _CODES),
            ("action_type", _ACTIONS),
            ("expected_condition_kind", _CONDITIONS),
        ):
            if payload[key] not in allowed:
                payload[key] = "other" if key == "code" else "unknown"
        return payload


def diagnostic_archive(
    run_id: str, details: dict[str, object], redactor: StructuredRedactor
) -> SanitizedEvidence:
    """One bounded, scanned snapshot; never a raw browser trace or screenshot archive."""
    parse_id(run_id, EntityKind.RUN)
    payload = {"run_id": run_id, **ExecutionDiagnostic.model_validate(details).safe_payload()}
    sanitized = redactor.sanitize_json(
        payload, dict.fromkeys(payload, DataClassification.OPERATIONAL), run_salt=run_id
    )
    if len(sanitized.content) > 64 * 1024:
        raise ValueError("diagnostic exceeds the snapshot limit")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("diagnostic.json", sanitized.content)
    return SanitizedEvidence(
        stream.getvalue(),
        "application/zip",
        (*sanitized.redaction_directives, "retain:value-free-diagnostic"),
    )


def bounded_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None
