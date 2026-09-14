"""Application-independent safety checks for replaying discovered scenario prefixes."""

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    Condition,
    IdentityMatchesCondition,
    NotCondition,
    Step,
)


def _contains_identity(condition: Condition) -> bool:
    if isinstance(condition, IdentityMatchesCondition):
        return True
    if isinstance(condition, AllCondition | AnyCondition):
        return any(_contains_identity(item) for item in condition.conditions)
    if isinstance(condition, NotCondition):
        return _contains_identity(condition.condition)
    return False


def scenario_expected_condition(step: Step, proposed: Condition | None) -> Condition | None:
    """Keep existing identity guards and the model's new assertion, including nested guards.

    Preserve composite semantics; flattening an OR into mandatory identities would change the
    learned program. Successful-path conditions without identity checks may legitimately differ
    on an exception branch, so they are not copied blindly.
    """
    required = tuple(item for item in step.postconditions if _contains_identity(item))
    if proposed is not None and proposed not in required:
        required += (proposed,)
    if not required:
        return None
    return required[0] if len(required) == 1 else AllCondition(kind="all", conditions=required)
