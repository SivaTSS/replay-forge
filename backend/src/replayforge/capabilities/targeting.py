"""Bind identity data transiently; never interpolate selectors, code, or geometry."""

from typing import Any

from replayforge.capabilities.models import (
    InputTextCandidate,
    LocatorBundle,
    ObjectContract,
    OcrRelativeCandidate,
    RenderedTextCandidate,
    VisualLocatorCandidate,
)
from replayforge.capabilities.values import (
    ContractValidationError,
    binding_classification,
    resolve_input,
)
from replayforge.policy.types import DataClassification


def target_input_paths(target: LocatorBundle) -> tuple[str, ...]:
    return tuple(
        candidate.value.path
        for candidate in target.visual_candidates
        if isinstance(candidate, InputTextCandidate)
    )


def bind_target_inputs(
    target: LocatorBundle,
    inputs: dict[str, Any],
    contract: ObjectContract | None,
    forbidden: frozenset[DataClassification],
) -> LocatorBundle:
    """Resolve exact-match text; the caller must retain the original symbolic bundle."""
    candidates: list[VisualLocatorCandidate] = []
    for candidate in target.visual_candidates:
        if not isinstance(candidate, InputTextCandidate):
            candidates.append(candidate)
            continue
        path = candidate.value.path
        classification = (
            binding_classification(contract, path, forbidden) if contract is not None else None
        )
        if classification is None or classification in forbidden:
            raise ContractValidationError(path, "target_input_forbidden")
        value = resolve_input(inputs, path)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ContractValidationError(path, "target_input_invalid")
        if candidate.target_text is None:
            candidates.append(RenderedTextCandidate(strategy="rendered_text", value=value))
        else:
            assert candidate.relation is not None
            candidates.append(
                OcrRelativeCandidate(
                    strategy="ocr_relative",
                    anchor=value,
                    target_text=candidate.target_text,
                    relation=candidate.relation,
                )
            )
    return target.model_copy(update={"visual_candidates": tuple(candidates)})
