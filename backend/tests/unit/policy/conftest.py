from datetime import UTC, datetime

import pytest

from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import DataClassification, Risk
from replayforge.shared.clock import FrozenClock


@pytest.fixture
def policy() -> EffectivePolicy:
    layer = PolicyLayer(
        name="platform",
        allowed_origins=frozenset({"http://demo.local:3001"}),
        allowed_route_patterns=frozenset(
            {"/members/search", "/members/:member_id", "/accounts/*/details"}
        ),
        allowed_action_types=frozenset({"click", "type", "extract"}),
        maximum_risk=Risk.READ_ONLY,
        forbidden_field_classes=frozenset(
            {DataClassification.CREDENTIAL, DataClassification.SECRET}
        ),
    )
    return EffectivePolicy.intersect(layer)


@pytest.fixture
def evaluator() -> PolicyEvaluator:
    return PolicyEvaluator(FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC)))
