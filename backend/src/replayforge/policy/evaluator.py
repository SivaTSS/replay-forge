"""Deny-by-default policy evaluator for automation and human input."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict
from urllib.parse import urlsplit

from replayforge.policy.models import (
    ActionContext,
    EffectivePolicy,
    PolicyDecision,
    PrincipalType,
    RunMode,
)
from replayforge.policy.types import RISK_RANK, Decision, Risk
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityKind, new_id

_SENSITIVE_TARGET_WORDS = frozenset(
    {"transfer", "wire", "close account", "approve", "reveal", "full account"}
)
_IRREVERSIBLE_TARGET_WORDS = frozenset(
    {"submit transfer", "send wire", "close account", "delete account"}
)


class _DecisionContext(TypedDict):
    matched_layers: tuple[str, ...]
    effective_risk: Risk
    evaluated_at: datetime


def _canonical_origin(origin: str) -> str | None:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return None
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def _route_matches(route: str, pattern: str) -> bool:
    if not route.startswith("/") or "?" in route or "#" in route:
        return False
    segments = pattern.strip("/").split("/") if pattern != "/" else []
    parts: list[str] = []
    for segment in segments:
        if segment == "*" or (segment.startswith(":") and len(segment) > 1):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(segment))
    expression = r"^/" + "/".join(parts) + r"/?$"
    return re.fullmatch(expression, route) is not None


def _infer_risk(context: ActionContext) -> Risk:
    description = context.target_description.casefold()
    if any(word in description for word in _IRREVERSIBLE_TARGET_WORDS):
        inferred = Risk.IRREVERSIBLE
    elif any(word in description for word in _SENSITIVE_TARGET_WORDS):
        inferred = Risk.SENSITIVE
    elif context.registered_target_risk is not None:
        inferred = context.registered_target_risk
    elif context.action_type in {"type", "select"}:
        inferred = Risk.REVERSIBLE
    else:
        inferred = Risk.READ_ONLY
    return max(context.declared_risk, inferred, key=RISK_RANK.__getitem__)


@dataclass(frozen=True, slots=True)
class PolicyEvaluator:
    clock: Clock

    def evaluate(self, policy: EffectivePolicy, context: ActionContext) -> PolicyDecision:
        risk = _infer_risk(context)
        common: _DecisionContext = {
            "matched_layers": policy.layer_names,
            "effective_risk": risk,
            "evaluated_at": self.clock.now(),
        }

        expected_owner = (
            f"human:{context.principal_id}"
            if context.principal_type is PrincipalType.HUMAN
            else "automation"
        )
        if context.control_owner != expected_owner:
            return self._deny(
                "control_owner_mismatch",
                "The principal does not own the live session control lease.",
                **common,
            )
        if (
            context.principal_type is PrincipalType.HUMAN
            and context.run_mode is not RunMode.HUMAN_CONTROL
        ):
            return self._deny(
                "principal_mode_mismatch",
                "Human input is accepted only while the run is in human-control mode.",
                **common,
            )

        origin = _canonical_origin(context.origin)
        if origin is None or origin not in policy.allowed_origins:
            return self._deny(
                "origin_not_allowed", "The current origin is not allowlisted.", **common
            )
        if not any(
            _route_matches(context.route, pattern) for pattern in policy.allowed_route_patterns
        ):
            return self._deny(
                "route_not_allowed", "The current route is not allowlisted.", **common
            )
        if context.action_type not in policy.allowed_action_types:
            return self._deny("action_not_allowed", "The action type is not allowlisted.", **common)
        if context.field_classification in policy.forbidden_field_classes:
            return self._deny(
                "field_classification_forbidden",
                "Policy forbids input into this field classification.",
                **common,
            )
        if risk is Risk.IRREVERSIBLE:
            return self._deny(
                "irreversible_action_blocked",
                "Irreversible actions are blocked in this environment.",
                **common,
            )
        if RISK_RANK[risk] > RISK_RANK[policy.maximum_risk]:
            return self._deny(
                "risk_exceeds_policy",
                "The independently classified risk exceeds the effective policy ceiling.",
                **common,
            )
        if risk is Risk.SENSITIVE:
            return PolicyDecision(
                id=new_id(EntityKind.DECISION),
                decision=Decision.REQUIRE_HUMAN_APPROVAL,
                reason_code="sensitive_action_requires_approval",
                explanation="A scoped, single-use human approval is required.",
                required_evidence=("policy_context", "pre_action_screenshot"),
                redaction_directives=("mask_customer_identifiers",),
                **common,
            )
        return PolicyDecision(
            id=new_id(EntityKind.DECISION),
            decision=Decision.ALLOW,
            reason_code="policy_allowed",
            explanation="The action satisfies every effective policy layer.",
            required_evidence=("action_intent", "action_result"),
            redaction_directives=("mask_customer_identifiers",),
            **common,
        )

    @staticmethod
    def _deny(
        reason_code: str,
        explanation: str,
        *,
        matched_layers: tuple[str, ...],
        effective_risk: Risk,
        evaluated_at: datetime,
    ) -> PolicyDecision:
        return PolicyDecision(
            id=new_id(EntityKind.DECISION),
            decision=Decision.DENY,
            reason_code=reason_code,
            explanation=explanation,
            matched_layers=matched_layers,
            effective_risk=effective_risk,
            required_evidence=("policy_context",),
            redaction_directives=("mask_customer_identifiers",),
            evaluated_at=evaluated_at,
        )
