"""Reject unbound condition operands before a discovery assertion can execute."""

from typing import Any

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    Condition,
    IdentityMatchesCondition,
    NotCondition,
    OutputValidCondition,
)
from replayforge.capabilities.values import ContractValidationError, resolve_input
from replayforge.surfaces.models import SurfaceError


def validate_condition_bindings(
    condition: Condition, outputs: dict[str, Any], inputs: dict[str, Any]
) -> None:
    """Binding errors are replannable; value comparisons remain the surface's job."""
    if isinstance(condition, AllCondition | AnyCondition):
        for child in condition.conditions:
            validate_condition_bindings(child, outputs, inputs)
    elif isinstance(condition, NotCondition):
        validate_condition_bindings(condition.condition, outputs, inputs)
    elif isinstance(condition, OutputValidCondition | IdentityMatchesCondition):
        output = (
            condition.output
            if isinstance(condition, OutputValidCondition)
            else condition.extracted_output
        )
        if output not in outputs:
            raise SurfaceError(
                "condition_output_unbound",
                "The condition requires an output that has not been captured.",
                recoverable=True,
                effect_absent=True,
            )
        if isinstance(condition, IdentityMatchesCondition):
            try:
                resolve_input(inputs, condition.input_path)
            except ContractValidationError as error:
                raise SurfaceError(
                    "condition_input_unbound",
                    "The condition requires an unavailable symbolic input binding.",
                    recoverable=True,
                    effect_absent=True,
                ) from error
