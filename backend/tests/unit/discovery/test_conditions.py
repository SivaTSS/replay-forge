from typing import Any

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    IdentityMatchesCondition,
    NotCondition,
    OutputValidCondition,
)
from replayforge.discovery.conditions import validate_condition_bindings
from replayforge.surfaces.models import SurfaceError


@pytest.mark.parametrize("wrapper", ["all", "any", "not"])
def test_missing_output_cannot_hide_in_a_nested_condition(wrapper: str) -> None:
    child = OutputValidCondition(kind="output_valid", output="result")
    condition = (
        AllCondition(kind="all", conditions=(child,))
        if wrapper == "all"
        else AnyCondition(kind="any", conditions=(child,))
        if wrapper == "any"
        else NotCondition(kind="not", condition=child)
    )
    with pytest.raises(SurfaceError) as error:
        validate_condition_bindings(condition, {}, {})
    assert error.value.code == "condition_output_unbound"
    assert error.value.recoverable and error.value.effect_absent


@pytest.mark.parametrize("inputs", [{}, {"request": {}}])
def test_identity_requires_an_available_nested_input(inputs: dict[str, Any]) -> None:
    condition = IdentityMatchesCondition(
        kind="identity_matches", extracted_output="date", input_path="request.date"
    )
    with pytest.raises(SurfaceError) as error:
        validate_condition_bindings(condition, {"date": "synthetic-observed"}, inputs)
    assert error.value.code == "condition_input_unbound"
    assert "synthetic-observed" not in str(error.value)


def test_bound_but_unequal_values_are_left_to_the_actual_condition_evaluator() -> None:
    validate_condition_bindings(
        IdentityMatchesCondition(
            kind="identity_matches", extracted_output="date", input_path="date"
        ),
        {"date": "observed"},
        {"date": "different"},
    )
