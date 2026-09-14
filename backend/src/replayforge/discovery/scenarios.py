"""Application-independent safety checks for replaying discovered scenario prefixes."""

from replayforge.capabilities.conditions import contains_identity
from replayforge.capabilities.models import AllCondition, Condition, Step


def scenario_expected_condition(step: Step, proposed: Condition | None) -> Condition | None:
    """Keep existing identity guards and the model's new assertion, including nested guards.

    Preserve composite semantics; flattening an OR into mandatory identities would change the
    learned program. Successful-path conditions without identity checks may legitimately differ
    on an exception branch, so they are not copied blindly.
    """
    required = tuple(item for item in step.postconditions if contains_identity(item))
    if proposed is not None and proposed not in required:
        required += (proposed,)
    if not required:
        return None
    return required[0] if len(required) == 1 else AllCondition(kind="all", conditions=required)
