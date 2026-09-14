"""Positive retention schema for operational journal details.

Runtime codes and counters are diagnostic evidence. Unstructured messages, UI facts,
and newly introduced fields are not automatically trusted merely because they lack
a password-shaped key. Extend this schema deliberately when adding an event field.
"""

from __future__ import annotations

import re

from replayforge.capabilities.models import LocatorStrategy
from replayforge.policy.types import DataClassification

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,127}")
_CODE_FIELDS = frozenset(
    {
        "code",
        "reason",
        "error_code",
        "recovery_id",
        "resume_at",
        "output",
        "input_binding",
        "output_binding",
        "transform",
        "source_step_id",
    }
)
_COUNT_FIELDS = frozenset(
    {
        "attempt",
        "uses",
        "use",
        "lease_version",
        "frame_depth",
        "client_sequence",
        "source_frame_sequence",
        "viewport_height",
        "viewport_width",
        "x",
        "y",
        "character_count",
        "after_step_count",
        "step_ordinal",
        "expected_count",
        "observed_count",
        "max_attempts",
    }
)
_ENUM_FIELDS = {
    "phase": {"before_dispatch", "after_dispatch", "completion", "unknown"},
    "dispatch_state": {"not_attempted", "attempted", "unknown"},
    "condition_kind": {
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
    },
    "decision": {"allow", "deny", "require_human_approval"},
    "disposition": {"continue", "business_outcome"},
    "proposal_kind": {"act", "complete", "escalate", "recorded_action", "branch"},
    "action_type": {
        "click",
        "type",
        "select",
        "press_keys",
        "scroll",
        "wait_for",
        "assert",
        "extract",
        "checkpoint",
        "navigate",
        "switch_context",
        "unknown",
    },
    "declared_risk": {"read_only", "reversible", "sensitive", "irreversible"},
    "evidence_frame": {"captured", "unavailable", "not_applicable"},
    "input_type": {"pointer", "key", "text"},
    "key": {
        "Enter",
        "Tab",
        "Shift+Tab",
        "Escape",
        "Backspace",
        "Delete",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
    },
}
_STRATEGIES = frozenset(
    {
        *(strategy.value for strategy in LocatorStrategy),
        "ocr_text",
        "ocr_relative",
        "rendered_text",
        "rendered_labeled_control",
        "rendered_field_value",
        "rendered_group_image",
        "input_text",
    }
)


def event_detail_classifications(details: dict[str, object]) -> dict[str, DataClassification]:
    """Unknown fields and wrong-shaped operational values are personal by default."""

    classifications = {}
    for key, value in details.items():
        if key == "operator_id":
            # A locally supplied operator label may be a person's name or email address.
            classifications[key] = DataClassification.CUSTOMER_IDENTIFIER
            continue
        safe = (
            (
                key in _CODE_FIELDS
                and isinstance(value, str)
                and _IDENTIFIER.fullmatch(value) is not None
            )
            or (key in _COUNT_FIELDS and type(value) is int and 0 <= value <= 1_000_000)
            or (
                key
                in {"effect_absent", "target_present", "retry_error_allowed", "recovery_checked"}
                and isinstance(value, bool)
            )
            or (key in _ENUM_FIELDS and isinstance(value, str) and value in _ENUM_FIELDS[key])
            or (
                key == "expected_condition_kind"
                and isinstance(value, str)
                and value in _ENUM_FIELDS["condition_kind"]
            )
            or (
                key in {"session_id", "intervention_id"}
                and isinstance(value, str)
                and re.fullmatch(r"(?:ses|int)_[0-9a-f]{32}", value) is not None
            )
            or (
                key == "locator_strategies"
                and isinstance(value, list)
                and all(isinstance(item, str) and item in _STRATEGIES for item in value)
            )
        )
        classifications[key] = (
            DataClassification.OPERATIONAL if safe else DataClassification.PERSONAL
        )
    return classifications


def terminal_classifications(
    result: dict[str, object], declared: dict[str, DataClassification]
) -> dict[str, DataClassification]:
    """Keep result structure, but never implicitly retain diagnostics or output values."""

    operational = {
        "status",
        "run_id",
        "code",
        "recoverable",
        "step_id",
        "evidence_manifest",
        "capability",
        "checkpoint",
        "artifact_content_hash",
        "diagnostic_trace",
    }
    classifications = {
        key: DataClassification.OPERATIONAL if key in operational else DataClassification.PERSONAL
        for key in result
        if key != "outputs"
    }

    def classify_outputs(value: object, path: str) -> None:
        # A classified parent cannot be weakened by descendants.
        if path in declared:
            classifications[path] = declared[path]
        elif isinstance(value, dict):
            for key, item in value.items():
                classify_outputs(item, f"{path}.{key}")
        else:
            classifications[path] = DataClassification.PERSONAL

    if "outputs" in result:
        classify_outputs(result["outputs"], "outputs")
    for path, classification in declared.items():
        if path.startswith("outputs.") or classification not in {
            DataClassification.OPERATIONAL,
            DataClassification.PUBLIC,
        }:
            classifications[path] = classification
    return classifications
