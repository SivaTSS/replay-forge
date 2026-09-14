"""OpenAI Responses API adapter for schema-constrained discovery proposals."""

from __future__ import annotations

import json
import logging
import time
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from replayforge.capabilities.models import (
    InputValue,
    JsonValueType,
    LiteralValue,
    MatchMode,
    ObjectContract,
    PersistenceMode,
    ValueSchema,
)
from replayforge.discovery.models import (
    CapabilityDraftSpec,
    CompleteProposal,
    DiscoveryProposal,
    EscalateProposal,
    PlanningContext,
    ProviderContext,
)
from replayforge.discovery.ports import ModelProviderError
from replayforge.observability.model_calls import (
    ModelCallMetric,
    ModelCallTelemetry,
    ModelUsage,
    NoOpModelCallTelemetry,
    ProviderErrorCategory,
)
from replayforge.policy.types import RISK_RANK, DataClassification, Risk
from replayforge.providers.policy import ModelPolicy

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """You select exactly one safe next step for UI workflow discovery.
Return only the provided structured proposal. Use symbolic input paths, never literal customer
values. When visual_tokens is non-empty, prefer rendered semantic candidates. For an icon-only
click target that OCR cannot name, you may provide one transient coordinates candidate covering
its tight bounding box and set capture_group_label to the unique rendered label for its row/card;
discovery converts that temporary region into a content-addressed image signature before
recording it. Never use coordinates for type or extract.
When actionable text repeats anywhere on screen, use ocr_relative with a unique nearby label as
anchor, the action text as target_text, and the observed spatial relation. Never use coordinates
for a text-labeled control.
The anchor itself must occur exactly once. right_of requires row alignment; below requires
column alignment. More than one matching target within that relation remains ambiguous.
Rendered text, labels, and anchors must use an exact complete string from visual_tokens; never use
a partial word or contains matching.
Do not navigate to arbitrary URLs. Escalate when state is ambiguous, risky, or stuck.
Declare risk conservatively. Use click, type, select, press_keys, scroll, wait_for, assert, and
extract only when the registered action allowlist contains them. Extract every required output
using its exact field name, and
complete only when every required output and the requested result are visibly verified.
Conditions use operand as the route pattern, visible text, or output name. Identity conditions use
operand for the extracted output and secondary_operand for the input path.
When frame_titles is non-empty, controls represented by the inner application observation must
use target.scope.frame_path with a title locator matching the relevant frame title exactly.
Never exceed maximum_risk. Typing into a search/query field whose operation only retrieves data,
clicking controls that only navigate to retrieved data, and extracting displayed data are
read_only. Opening a review or confirmation screen without applying its mutation is also
read_only. A temporary state change with an explicit visible inverse is reversible; data being
financial or personal does not by itself make an action sensitive. Target descriptions must name
only the control or displayed value, not broader data.
Never click a static displayed value. On a details view, use extract once for each required
output field, then complete only after every required field has been captured. When selecting a
role_name target, use the exact role and name from actionable_controls and require count one.
For a relative_text following_value extraction, use the exact anchor from extractable_fields and
require count one. Extractable field labels are structural names only; their values are omitted.
For extract actions, use each required output field's preferred_transform exactly. Extract only
remaining_output_fields. Never extract a field listed in captured_output_fields or any undeclared
output. Complete when remaining_output_fields is empty and the requested result is verified."""


_PLAN_INSTRUCTIONS = """Plan a reusable capability from the natural-language goal and initial
surface observation. Choose a short snake_case operation_slug, describe the requested operation,
and list every output the caller should receive. Output names must be stable semantic names, not
screen labels or customer values. All UI extractions are normalized text, so declare every output
as type string. Never invent parsing formats, regexes, enums, constants, or customer values. Never
declare credential or secret fields. Be conservative about
risk: entering search or filter criteria that only retrieves data remains read_only. A mutation may
be reversible only when the observed workflow exposes an explicit inverse operation that restores
the prior state; otherwise it is sensitive or irreversible."""


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderOutputField(ProviderModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    type: Literal[JsonValueType.STRING]
    description: str = Field(min_length=1, max_length=500)
    data_classification: DataClassification = DataClassification.PERSONAL
    required: Literal[True] = True


class CapabilityPlanProposal(ProviderModel):
    operation_slug: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_000)
    outputs: tuple[ProviderOutputField, ...] = Field(min_length=1, max_length=50)
    risk: Risk
    tags: tuple[str, ...] = Field(default=(), max_length=20)


class CapabilityPlanEnvelope(ProviderModel):
    proposal: CapabilityPlanProposal


class ProviderClickAction(ProviderModel):
    kind: Literal["click"]


class ProviderTypeAction(ProviderModel):
    kind: Literal["type"]
    value: InputValue | LiteralValue
    clear: bool = True


class ProviderExtractAction(ProviderModel):
    kind: Literal["extract"]
    output: str
    transform: Literal["text", "trim", "lowercase", "decimal", "date-time"] = "trim"


class ProviderSelectAction(ProviderModel):
    kind: Literal["select"]
    option: InputValue | LiteralValue


class ProviderPressKeysAction(ProviderModel):
    kind: Literal["press_keys"]
    keys: tuple[str, ...] = Field(min_length=1, max_length=4)


class ProviderScrollAction(ProviderModel):
    kind: Literal["scroll"]
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(gt=0, le=2_000)


class ProviderCondition(ProviderModel):
    """Flat provider wire shape; the domain adapter validates the kind-specific fields."""

    kind: Literal["route", "text", "rendered_text", "output_valid", "identity_matches"]
    operand: str = Field(min_length=1, max_length=500)
    secondary_operand: str | None = Field(default=None, min_length=1, max_length=200)
    match: MatchMode | None = None


class ProviderWaitForAction(ProviderModel):
    kind: Literal["wait_for"]
    condition: ProviderCondition


class ProviderAssertAction(ProviderModel):
    kind: Literal["assert"]
    condition: ProviderCondition


class ProviderNavigateAction(ProviderModel):
    kind: Literal["navigate"]
    entry_point: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


class ProviderSwitchContextAction(ProviderModel):
    kind: Literal["switch_context"]
    context: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class ProviderRoleNameCandidate(ProviderModel):
    strategy: Literal["role_name"]
    role: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    match: Literal["exact", "contains"] = "exact"
    expected_count: Literal[1] = 1


class ProviderInputCandidate(ProviderModel):
    strategy: Literal["label", "placeholder"]
    value: str = Field(min_length=1, max_length=200)
    match: Literal["exact", "contains"] = "exact"
    expected_count: Literal[1] = 1


class ProviderDisplayedTextCandidate(ProviderModel):
    strategy: Literal["text", "title"]
    value: str = Field(min_length=1, max_length=200)
    match: Literal["exact", "contains"] = "exact"
    expected_count: Literal[1] = 1


class ProviderFollowingValueCandidate(ProviderModel):
    strategy: Literal["relative_text"]
    anchor: str = Field(min_length=1, max_length=200)
    relation: Literal["following_value"]
    element: str = Field(min_length=1, max_length=100)
    expected_count: Literal[1] = 1


class ProviderOcrTextCandidate(ProviderModel):
    strategy: Literal["ocr_text"]
    value: str = Field(min_length=1, max_length=200)
    match: Literal[MatchMode.EXACT] = MatchMode.EXACT
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)
    expected_count: Literal[1] = 1


class ProviderOcrRelativeCandidate(ProviderModel):
    strategy: Literal["ocr_relative"]
    anchor: str = Field(min_length=1, max_length=200)
    anchor_match: Literal[MatchMode.EXACT] = MatchMode.EXACT
    target_text: str = Field(min_length=1, max_length=200)
    relation: Literal["right_of", "below", "same_row"]
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)
    expected_count: Literal[1] = 1


class ProviderRenderedTextCandidate(ProviderModel):
    strategy: Literal["rendered_text"]
    value: str = Field(min_length=1, max_length=200)
    match: Literal[MatchMode.EXACT] = MatchMode.EXACT


class ProviderRenderedLabeledControlCandidate(ProviderModel):
    strategy: Literal["rendered_labeled_control"]
    label: str = Field(min_length=1, max_length=200)
    label_match: Literal[MatchMode.EXACT] = MatchMode.EXACT
    control_kind: Literal["text_input"]


class ProviderRenderedFieldValueCandidate(ProviderModel):
    strategy: Literal["rendered_field_value"]
    label: str = Field(min_length=1, max_length=200)
    label_match: Literal[MatchMode.EXACT] = MatchMode.EXACT


class ProviderRenderedGroupImageCandidate(ProviderModel):
    strategy: Literal["rendered_group_image"]
    group_label: str = Field(min_length=1, max_length=200)
    group_label_match: Literal[MatchMode.EXACT] = MatchMode.EXACT
    asset_key: str = Field(pattern=r"^asset://sha256/[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProviderFrameTitleCandidate(ProviderModel):
    strategy: Literal["title"]
    value: str = Field(min_length=1, max_length=200)
    match: Literal["exact"] = "exact"
    expected_count: Literal[1] = 1


class ProviderCoordinateCandidate(ProviderModel):
    strategy: Literal["coordinates"]
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    capture_group_label: str | None = Field(default=None, min_length=1, max_length=200)
    portability: Literal["low"] = "low"
    expected_count: Literal[1] = 1


class ProviderFrameLocator(ProviderModel):
    locator: ProviderFrameTitleCandidate


class ProviderLocatorScope(ProviderModel):
    window: Literal["primary"] = "primary"
    frame_path: tuple[ProviderFrameLocator, ...] = Field(default=(), max_length=4)


class ProviderLocatorBundleBase(ProviderModel):
    description: str = Field(min_length=1, max_length=200)
    registered_risk: Literal["read_only"] = "read_only"
    scope: ProviderLocatorScope = Field(default_factory=ProviderLocatorScope)


class ProviderClickLocatorBundle(ProviderLocatorBundleBase):
    visual_candidates: tuple[
        ProviderOcrTextCandidate
        | ProviderOcrRelativeCandidate
        | ProviderRenderedTextCandidate
        | ProviderRenderedGroupImageCandidate,
        ...,
    ] = ()
    candidates: tuple[ProviderRoleNameCandidate | ProviderCoordinateCandidate, ...] = Field(
        default=(), max_length=5
    )


class ProviderTypeLocatorBundle(ProviderLocatorBundleBase):
    visual_candidates: tuple[
        ProviderOcrRelativeCandidate | ProviderRenderedLabeledControlCandidate, ...
    ] = ()
    candidates: tuple[ProviderRoleNameCandidate | ProviderInputCandidate, ...] = Field(
        default=(), max_length=5
    )


class ProviderExtractLocatorBundle(ProviderLocatorBundleBase):
    visual_candidates: tuple[
        ProviderOcrRelativeCandidate | ProviderRenderedFieldValueCandidate, ...
    ] = ()
    candidates: tuple[
        ProviderRoleNameCandidate
        | ProviderDisplayedTextCandidate
        | ProviderFollowingValueCandidate,
        ...,
    ] = Field(default=(), max_length=5)


class ProviderActProposalBase(ProviderModel):
    kind: Literal["act"]
    rationale: str = Field(min_length=1, max_length=500)
    expected_effect: str = Field(min_length=1, max_length=500)
    declared_risk: Risk
    confidence: float = Field(ge=0, le=1)


class ProviderClickProposal(ProviderActProposalBase):
    action: ProviderClickAction
    target: ProviderClickLocatorBundle


class ProviderTypeProposal(ProviderActProposalBase):
    action: ProviderTypeAction
    target: ProviderTypeLocatorBundle


class ProviderExtractProposal(ProviderActProposalBase):
    action: ProviderExtractAction
    target: ProviderExtractLocatorBundle


class ProviderSelectProposal(ProviderActProposalBase):
    action: ProviderSelectAction
    target: ProviderTypeLocatorBundle


class ProviderPressKeysProposal(ProviderActProposalBase):
    action: ProviderPressKeysAction
    target: ProviderClickLocatorBundle | None = None


class ProviderScrollProposal(ProviderActProposalBase):
    action: ProviderScrollAction
    target: ProviderClickLocatorBundle | None = None


class ProviderWaitForProposal(ProviderActProposalBase):
    action: ProviderWaitForAction
    target: None = None


class ProviderAssertProposal(ProviderActProposalBase):
    action: ProviderAssertAction
    target: None = None


class ProviderNavigateProposal(ProviderActProposalBase):
    action: ProviderNavigateAction
    target: None = None


class ProviderSwitchContextProposal(ProviderActProposalBase):
    action: ProviderSwitchContextAction
    target: None = None


class ProposalEnvelope(ProviderModel):
    proposal: (
        ProviderClickProposal
        | ProviderTypeProposal
        | ProviderExtractProposal
        | ProviderSelectProposal
        | ProviderPressKeysProposal
        | ProviderScrollProposal
        | ProviderWaitForProposal
        | ProviderAssertProposal
        | ProviderNavigateProposal
        | ProviderSwitchContextProposal
        | CompleteProposal
        | EscalateProposal
    )


_DISCOVERY_PROPOSAL: TypeAdapter[DiscoveryProposal] = TypeAdapter(DiscoveryProposal)


def _condition_payload(condition: dict[str, Any]) -> dict[str, Any]:
    kind = condition.get("kind")
    if kind == "route":
        return {"kind": kind, "pattern": condition.get("operand")}
    if kind in {"text", "rendered_text"}:
        return {
            "kind": kind,
            "value": condition.get("operand"),
            "match": condition.get("match") or MatchMode.EXACT,
        }
    if kind == "output_valid":
        return {"kind": kind, "output": condition.get("operand")}
    if kind == "identity_matches":
        return {
            "kind": kind,
            "extracted_output": condition.get("operand"),
            "input_path": condition.get("secondary_operand"),
        }
    return condition


def _proposal_payload(proposal: BaseModel) -> dict[str, Any]:
    payload = proposal.model_dump(mode="python", exclude_none=True)
    action = payload.get("action")
    if isinstance(action, dict) and isinstance(action.get("condition"), dict):
        action["condition"] = _condition_payload(action["condition"])
    return payload


def _input_contract(inputs: dict[str, Any]) -> ObjectContract:
    properties: dict[str, ValueSchema] = {}
    for name, value in inputs.items():
        if isinstance(value, bool):
            value_type = JsonValueType.BOOLEAN
        elif isinstance(value, int):
            value_type = JsonValueType.INTEGER
        elif isinstance(value, str):
            value_type = JsonValueType.STRING
        else:
            raise ValueError("discovery inputs must be local primitive values")
        classification = (
            DataClassification.CUSTOMER_IDENTIFIER
            if name.endswith("_id") or name == "id"
            else DataClassification.PERSONAL
        )
        properties[name] = ValueSchema(
            type=value_type,
            description=f"Invocation value for {name.replace('_', ' ')}.",
            data_classification=classification,
            persistence=PersistenceMode.REDACTED,
        )
    return ObjectContract(required=tuple(properties), properties=properties)


def _preferred_transform(schema: ValueSchema) -> str:
    if schema.format == "decimal":
        return "decimal"
    if schema.format == "date-time":
        return "date-time"
    if isinstance(schema.const, str) and schema.const == schema.const.lower():
        return "lowercase"
    return "trim"


def _output_requirement(name: str, schema: ValueSchema) -> dict[str, object]:
    return {
        "name": name,
        "type": schema.type.value,
        "format": schema.format,
        "const": schema.const,
        "enum": list(schema.enum),
        "preferred_transform": _preferred_transform(schema),
    }


class ParsedResponsePort(Protocol):
    @property
    def output_parsed(self) -> ProposalEnvelope | None: ...

    @property
    def usage(self) -> ResponseUsagePort: ...


class ResponseUsagePort(Protocol):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ResponsesPort(Protocol):
    def parse(self, **kwargs: Any) -> ParsedResponsePort: ...


class OpenAIClientPort(Protocol):
    @property
    def responses(self) -> ResponsesPort: ...


@dataclass(slots=True)
class OpenAIModelProvider:
    client: OpenAIClientPort
    policy: ModelPolicy
    telemetry: ModelCallTelemetry = field(default_factory=NoOpModelCallTelemetry)
    _calls_made: int = field(default=0, init=False, repr=False)

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        policy: ModelPolicy,
        telemetry: ModelCallTelemetry | None = None,
    ) -> OpenAIModelProvider:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        return cls(
            cast(OpenAIClientPort, OpenAI(api_key=api_key, max_retries=1)),
            policy,
            telemetry or NoOpModelCallTelemetry(),
        )

    def for_run(self) -> OpenAIModelProvider:
        return OpenAIModelProvider(self.client, self.policy, self.telemetry)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.policy.model

    def plan(self, context: PlanningContext) -> CapabilityDraftSpec:
        """Ask the model for task semantics before the action loop begins."""
        if not context.screenshot_png or len(context.screenshot_png) > self.policy.max_frame_bytes:
            raise ModelProviderError(
                "provider_frame_invalid",
                "The visual observation is empty or exceeds the provider frame limit.",
            )
        try:
            input_contract = _input_contract(context.inputs)
        except ValueError as error:
            raise ModelProviderError(
                "provider_contract_invalid",
                "Discovery inputs must be local primitive values.",
            ) from error
        if self._calls_made >= self.policy.max_model_calls_per_run:
            raise ModelProviderError(
                "provider_budget_exceeded",
                "The discovery run exhausted its reviewed model-call budget.",
            )
        request = {
            "goal": context.goal,
            "application_family": context.application_family,
            "entry_point": context.entry_point,
            "input_fields": sorted(context.inputs),
            "input_types": {
                name: (
                    "boolean"
                    if isinstance(value, bool)
                    else "integer"
                    if isinstance(value, int)
                    else "string"
                )
                for name, value in context.inputs.items()
            },
            "observation": {
                "route": context.observation.route,
                "landmarks": list(context.observation.landmarks),
                "frame_titles": list(context.observation.frame_titles),
            },
            "maximum_risk": context.maximum_risk.value,
            "allowed_action_types": sorted(context.allowed_action_types),
            "requested_capability_id": context.requested_capability_id,
        }
        input_content = [
            {"type": "input_text", "text": json.dumps(request, separators=(",", ":"))},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,"
                + b64encode(context.screenshot_png).decode("ascii"),
                "detail": "high",
            },
        ]
        self._calls_made += 1
        call_index = self._calls_made
        started_at = time.monotonic()
        try:
            response = self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": self.policy.reasoning_effort},
                instructions=_PLAN_INSTRUCTIONS,
                input=[{"role": "user", "content": input_content}],
                text_format=CapabilityPlanEnvelope,
                max_output_tokens=self.policy.max_output_tokens,
                store=False,
                tools=[],
                parallel_tool_calls=False,
                timeout=self.policy.timeout_seconds,
            )
        except Exception as error:
            category, status_code, error_code = _safe_provider_error_details(error)
            logger.warning(
                "provider plan request failed: category=%s status=%s code=%s",
                category,
                status_code,
                error_code,
            )
            self._record_metric(
                call_index,
                started_at,
                "provider_error",
                error_category=category,
                provider_status_code=status_code,
                provider_error_code=error_code,
            )
            raise ModelProviderError(
                "provider_unavailable",
                "The model provider could not produce a capability plan.",
            ) from error
        parsed = response.output_parsed
        if not isinstance(parsed, CapabilityPlanEnvelope):
            self._record_metric(call_index, started_at, "invalid_response", response.usage)
            raise ModelProviderError(
                "provider_response_invalid",
                "The model provider returned no valid capability plan.",
            )
        self._record_metric(call_index, started_at, "success", response.usage)
        proposal = parsed.proposal
        if RISK_RANK[proposal.risk] > RISK_RANK[context.maximum_risk]:
            raise ModelProviderError(
                "provider_contract_invalid",
                "The capability plan exceeds the registered risk ceiling.",
            )
        if len({field.name for field in proposal.outputs}) != len(proposal.outputs):
            raise ModelProviderError(
                "provider_contract_invalid",
                "The capability plan contains duplicate output fields.",
            )
        if any(
            field.data_classification in {DataClassification.CREDENTIAL, DataClassification.SECRET}
            for field in proposal.outputs
        ):
            raise ModelProviderError(
                "provider_contract_forbidden",
                "The capability plan requested a credential or secret output.",
            )
        output_properties = {
            field.name: ValueSchema(
                type=field.type,
                description=field.description,
                data_classification=field.data_classification,
                persistence=PersistenceMode.REDACTED,
            )
            for field in proposal.outputs
        }
        if not output_properties:
            raise ModelProviderError(
                "provider_contract_invalid",
                "The capability plan declared no required outputs.",
            )
        return CapabilityDraftSpec(
            operation_slug=proposal.operation_slug,
            name=proposal.name,
            description=proposal.description,
            inputs=input_contract,
            outputs=ObjectContract(
                required=tuple(output_properties),
                properties=output_properties,
            ),
            risk=proposal.risk,
            tags=proposal.tags,
        )

    def decide(self, context: ProviderContext) -> DiscoveryProposal:
        if not context.screenshot_png or len(context.screenshot_png) > self.policy.max_frame_bytes:
            raise ModelProviderError(
                "provider_frame_invalid",
                "The visual observation is empty or exceeds the provider frame limit.",
            )
        request = {
            "goal": context.goal,
            "input_fields": sorted(context.inputs),
            "required_output_fields": [
                _output_requirement(name, context.output_contract.properties[name])
                for name in context.required_output_names
            ],
            "captured_output_fields": list(context.captured_output_names),
            "remaining_output_fields": list(context.remaining_output_names),
            "observation": {
                "route": context.observation.route,
                "viewport": {
                    "width": context.observation.viewport.width,
                    "height": context.observation.viewport.height,
                },
                "fingerprint": context.observation.fingerprint,
                "landmarks": list(context.observation.landmarks),
                "frame_titles": list(context.observation.frame_titles),
                "actionable_controls": [
                    {"role": control.role, "name": control.name, "count": control.count}
                    for control in context.observation.actionable_controls
                ],
                "extractable_fields": [
                    {"label": field.label, "count": field.count}
                    for field in context.observation.extractable_fields
                ],
                "visual_tokens": [
                    {
                        "text": token.text,
                        "confidence": round(token.confidence, 4),
                        "box": {
                            "x": token.region.x,
                            "y": token.region.y,
                            "width": token.region.width,
                            "height": token.region.height,
                        },
                    }
                    for token in context.observation.visual_tokens
                ],
                "active_element": context.observation.active_element,
                "dialog_text": context.observation.dialog_text,
            },
            "recent_actions": list(context.action_history[-20:]),
            "allowed_action_types": sorted(context.allowed_action_types),
            "maximum_risk": context.maximum_risk.value,
        }
        input_content = [
            {
                "type": "input_text",
                "text": json.dumps(request, separators=(",", ":"), ensure_ascii=False),
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,"
                + b64encode(context.screenshot_png).decode("ascii"),
                "detail": "high",
            },
        ]
        if self._calls_made >= self.policy.max_model_calls_per_run:
            raise ModelProviderError(
                "provider_budget_exceeded",
                "The discovery run exhausted its reviewed model-call budget.",
            )
        self._calls_made += 1
        call_index = self._calls_made
        started_at = time.monotonic()
        try:
            response = self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": self.policy.reasoning_effort},
                instructions=_INSTRUCTIONS,
                input=[{"role": "user", "content": input_content}],
                text_format=ProposalEnvelope,
                max_output_tokens=self.policy.max_output_tokens,
                store=False,
                tools=[],
                parallel_tool_calls=False,
                timeout=self.policy.timeout_seconds,
            )
        except Exception as error:
            category, status_code, error_code = _safe_provider_error_details(error)
            logger.warning(
                "provider decision request failed: category=%s status=%s code=%s",
                category,
                status_code,
                error_code,
            )
            self._record_metric(
                call_index,
                started_at,
                "provider_error",
                error_category=category,
                provider_status_code=status_code,
                provider_error_code=error_code,
            )
            raise ModelProviderError(
                "provider_unavailable",
                "The model provider could not produce a discovery decision.",
            ) from error
        parsed = response.output_parsed
        if parsed is None:
            self._record_metric(call_index, started_at, "invalid_response", response.usage)
            raise ModelProviderError(
                "provider_response_invalid",
                "The model provider returned no valid structured discovery decision.",
            )
        self._record_metric(call_index, started_at, "success", response.usage)
        try:
            return _DISCOVERY_PROPOSAL.validate_python(_proposal_payload(parsed.proposal))
        except ValidationError as error:
            safe_errors = [
                {
                    "type": item["type"],
                    "location": ".".join(str(part) for part in item["loc"]),
                }
                for item in error.errors(
                    include_url=False, include_context=False, include_input=False
                )[:5]
            ]
            logger.warning("provider proposal failed domain validation: %s", safe_errors)
            raise ModelProviderError(
                "provider_response_invalid",
                "The model provider returned an unsupported discovery proposal.",
            ) from error

    def _record_metric(
        self,
        call_index: int,
        started_at: float,
        outcome: Literal["success", "provider_error", "invalid_response"],
        usage: ResponseUsagePort | None = None,
        *,
        error_category: ProviderErrorCategory | None = None,
        provider_status_code: int | None = None,
        provider_error_code: str | None = None,
    ) -> None:
        model_usage = (
            ModelUsage(usage.input_tokens, usage.output_tokens, usage.total_tokens)
            if usage is not None
            else None
        )
        try:
            self.telemetry.record(
                ModelCallMetric(
                    call_index=call_index,
                    latency_ms=max(0, round((time.monotonic() - started_at) * 1_000)),
                    outcome=outcome,
                    usage=model_usage,
                    error_category=error_category,
                    provider_status_code=provider_status_code,
                    provider_error_code=provider_error_code,
                )
            )
        except Exception:
            # Monitoring availability is enforced before a discovery run starts. A transient
            # exporter failure after a paid call must not discard a valid provider response.
            return


def _safe_provider_error_details(
    error: Exception,
) -> tuple[ProviderErrorCategory, int | None, str | None]:
    """Reduce provider failures to bounded operational fields without retaining messages."""
    raw_status = getattr(error, "status_code", None)
    status_code = raw_status if isinstance(raw_status, int) and 100 <= raw_status <= 599 else None
    raw_code = getattr(error, "code", None)
    error_code = raw_code if isinstance(raw_code, str) else None
    if status_code == 401:
        return "authentication", status_code, error_code
    if status_code == 403:
        return "permission", status_code, error_code
    if status_code == 429:
        return "rate_limit", status_code, error_code
    if status_code is not None and status_code >= 500:
        return "server", status_code, error_code
    if status_code is not None and status_code >= 400:
        return "request", status_code, error_code
    if isinstance(error, TimeoutError):
        return "timeout", status_code, error_code
    if isinstance(error, ConnectionError | OSError):
        return "connection", status_code, error_code
    return "unknown", status_code, error_code
