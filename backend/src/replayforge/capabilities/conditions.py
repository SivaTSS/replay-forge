"""Shared inspection of conditions without changing composite boolean semantics."""

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    Condition,
    IdentityMatchesCondition,
    NotCondition,
)


def contains_identity(condition: Condition) -> bool:
    if isinstance(condition, IdentityMatchesCondition):
        return True
    if isinstance(condition, AllCondition | AnyCondition):
        return any(contains_identity(item) for item in condition.conditions)
    if isinstance(condition, NotCondition):
        return contains_identity(condition.condition)
    return False


def surface_conditions(condition: Condition) -> tuple[Condition, ...]:
    if condition.kind in {"route", "text", "rendered_text", "visual_text", "element"}:
        return (condition,)
    if isinstance(condition, AllCondition | AnyCondition):
        return tuple(nested for item in condition.conditions for nested in surface_conditions(item))
    if isinstance(condition, NotCondition):
        return (condition,) if surface_conditions(condition.condition) else ()
    return ()
