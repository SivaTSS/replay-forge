from dataclasses import replace
from datetime import UTC, datetime

import pytest

from replayforge.policy.models import EffectivePolicy, PolicyDecision, PolicyLayer
from replayforge.policy.types import DataClassification, Decision, Risk
from replayforge.shared.ids import EntityKind, new_id


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


@pytest.mark.parametrize(
    "origin",
    ["http://host:invalid", "http://host:99999", "http://host?", "http://host#", "http://ho\tst"],
)
def test_policy_rejects_ambiguous_or_invalid_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="policy origin"):
        PolicyLayer(
            name="test",
            allowed_origins=frozenset({origin}),
            allowed_route_patterns=frozenset({"/"}),
            allowed_action_types=frozenset({"click"}),
            maximum_risk=Risk.READ_ONLY,
        )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"name": "Platform Layer"}, "stable identifier"),
        ({"allowed_origins": frozenset({"https://user@example.test"})}, "origins"),
        ({"allowed_route_patterns": frozenset({"members/search"})}, "absolute paths"),
        ({"allowed_action_types": frozenset({"Click"})}, "action types"),
    ],
)
def test_policy_layer_rejects_ambiguous_configuration(
    change: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "name": "platform",
        "allowed_origins": frozenset({"https://example.test"}),
        "allowed_route_patterns": frozenset({"/members/search"}),
        "allowed_action_types": frozenset({"click"}),
        "maximum_risk": Risk.READ_ONLY,
    }
    values.update(change)

    with pytest.raises(ValueError, match=message):
        PolicyLayer(**values)  # type: ignore[arg-type]


def test_policy_decision_requires_typed_identity_and_auditable_fields() -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    decision = PolicyDecision(
        id=new_id(EntityKind.DECISION),
        decision=Decision.ALLOW,
        reason_code="policy_allowed",
        explanation="All policy layers allow the action.",
        matched_layers=("platform",),
        effective_risk=Risk.READ_ONLY,
        required_evidence=("action_intent",),
        redaction_directives=("mask_customer_identifiers",),
        evaluated_at=now,
    )

    with pytest.raises(ValueError, match="dec identifier"):
        replace(decision, id=new_id(EntityKind.RUN))
    with pytest.raises(ValueError, match="required evidence"):
        replace(decision, required_evidence=())
    with pytest.raises(ValueError, match="offset"):
        replace(decision, evaluated_at=now.replace(tzinfo=None))
