import pytest

from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import DataClassification, Risk


def test_effective_policy_is_intersection_and_most_restrictive_risk() -> None:
    platform = PolicyLayer(
        name="platform",
        allowed_origins=frozenset({"https://one.example", "https://two.example"}),
        allowed_route_patterns=frozenset({"/members/search", "/members/:id"}),
        allowed_action_types=frozenset({"click", "type", "extract"}),
        maximum_risk=Risk.SENSITIVE,
    )
    capability = PolicyLayer(
        name="capability",
        allowed_origins=frozenset({"https://one.example"}),
        allowed_route_patterns=frozenset({"/members/search"}),
        allowed_action_types=frozenset({"click", "type"}),
        maximum_risk=Risk.READ_ONLY,
        forbidden_field_classes=frozenset({DataClassification.PERSONAL}),
    )

    effective = EffectivePolicy.intersect(platform, capability)

    assert effective.layer_names == ("platform", "capability")
    assert effective.allowed_origins == {"https://one.example"}
    assert effective.allowed_route_patterns == {"/members/search"}
    assert effective.allowed_action_types == {"click", "type"}
    assert effective.maximum_risk is Risk.READ_ONLY
    assert effective.forbidden_field_classes == {
        DataClassification.CREDENTIAL,
        DataClassification.SECRET,
        DataClassification.PERSONAL,
    }


def test_effective_policy_requires_at_least_one_layer() -> None:
    with pytest.raises(ValueError, match="at least one"):
        EffectivePolicy.intersect()
