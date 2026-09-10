from dataclasses import replace
from typing import Any

import pytest

from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import (
    ActionContext,
    EffectivePolicy,
    PolicyLayer,
    PrincipalType,
    RunMode,
)
from replayforge.policy.types import DataClassification, Decision, Risk


@pytest.fixture
def context() -> ActionContext:
    return ActionContext(
        principal_type=PrincipalType.AUTOMATION,
        principal_id="runtime",
        run_mode=RunMode.REPLAY,
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        origin="http://demo.local:3001",
        route="/members/search",
        action_type="click",
        target_description="Search button",
        declared_risk=Risk.READ_ONLY,
        registered_target_risk=Risk.READ_ONLY,
        control_owner="automation",
    )


def test_allow_returns_auditable_decision(
    evaluator: PolicyEvaluator, policy: EffectivePolicy, context: ActionContext
) -> None:
    result = evaluator.evaluate(policy, context)

    assert result.decision is Decision.ALLOW
    assert result.reason_code == "policy_allowed"
    assert result.effective_risk is Risk.READ_ONLY
    assert result.matched_layers == ("platform",)
    assert result.required_evidence == ("action_intent", "action_result")


def test_known_read_only_field_can_accept_search_input(
    evaluator: PolicyEvaluator, policy: EffectivePolicy, context: ActionContext
) -> None:
    result = evaluator.evaluate(
        policy,
        replace(context, action_type="type", target_description="Member ID field"),
    )

    assert result.decision is Decision.ALLOW


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"control_owner": "automation_paused"}, "control_owner_mismatch"),
        ({"origin": "https://evil.example"}, "origin_not_allowed"),
        ({"origin": "not-an-origin"}, "origin_not_allowed"),
        ({"route": "/admin"}, "route_not_allowed"),
        ({"route": "/members/search?member=12345"}, "route_not_allowed"),
        ({"action_type": "navigate"}, "action_not_allowed"),
        (
            {"action_type": "type", "field_classification": DataClassification.CREDENTIAL},
            "field_classification_forbidden",
        ),
        (
            {"target_description": "Submit transfer now"},
            "irreversible_action_blocked",
        ),
        (
            {
                "action_type": "type",
                "registered_target_risk": None,
                "target_description": "Unregistered field",
            },
            "risk_exceeds_policy",
        ),
    ],
)
def test_denial_precedence_returns_stable_reason(
    evaluator: PolicyEvaluator,
    policy: EffectivePolicy,
    context: ActionContext,
    changes: dict[str, Any],
    reason: str,
) -> None:
    result = evaluator.evaluate(policy, replace(context, **changes))

    assert result.decision is Decision.DENY
    assert result.reason_code == reason
    assert result.required_evidence == ("policy_context",)


def test_human_must_be_in_human_control_mode(
    evaluator: PolicyEvaluator, policy: EffectivePolicy, context: ActionContext
) -> None:
    human = replace(
        context,
        principal_type=PrincipalType.HUMAN,
        principal_id="operator-7",
        control_owner="human:operator-7",
    )

    result = evaluator.evaluate(policy, human)

    assert result.reason_code == "principal_mode_mismatch"


def test_sensitive_action_requires_approval(
    evaluator: PolicyEvaluator, context: ActionContext
) -> None:
    sensitive_layer = PolicyLayer(
        name="platform",
        allowed_origins=frozenset({context.origin}),
        allowed_route_patterns=frozenset({context.route}),
        allowed_action_types=frozenset({context.action_type}),
        maximum_risk=Risk.SENSITIVE,
    )
    policy = EffectivePolicy.intersect(sensitive_layer)

    result = evaluator.evaluate(
        policy, replace(context, target_description="Reveal full account number")
    )

    assert result.decision is Decision.REQUIRE_HUMAN_APPROVAL
    assert result.reason_code == "sensitive_action_requires_approval"


def test_route_patterns_match_one_segment_only(
    evaluator: PolicyEvaluator, policy: EffectivePolicy, context: ActionContext
) -> None:
    accepted = evaluator.evaluate(policy, replace(context, route="/members/12345"))
    rejected = evaluator.evaluate(policy, replace(context, route="/members/12345/private"))

    assert accepted.decision is Decision.ALLOW
    assert rejected.reason_code == "route_not_allowed"
