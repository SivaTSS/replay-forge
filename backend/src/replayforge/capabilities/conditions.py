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
