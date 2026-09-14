from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from replayforge.capabilities.models import InputValue, JsonValueType
from replayforge.discovery.models import (
    ActProposal,
    BranchProposal,
    CompleteProposal,
    ProviderContext,
)
from replayforge.discovery.ports import ModelProviderError
from replayforge.observability.model_calls import ModelCallMetric
from replayforge.policy.types import Risk
from replayforge.providers.openai import (
    OpenAIModelProvider,
    ProposalEnvelope,
    ProviderBranchProposal,
    ProviderClickAction,
    ProviderClickLocatorBundle,
    ProviderClickProposal,
    ProviderCondition,
    ProviderExtractLocatorBundle,
    ProviderFrameLocator,
    ProviderFrameTitleCandidate,
    ProviderInputCandidate,
    ProviderLocatorScope,
    ProviderOcrRelativeCandidate,
    ProviderOutputField,
    ProviderRecordedActionProposal,
    ProviderRenderedFieldValueCandidate,
    ProviderTypeAction,
    ProviderTypeLocatorBundle,
    ProviderTypeProposal,
    ScenarioPrefixEnvelope,
    ScenarioRecoveryEnvelope,
    ScenarioStartEnvelope,
)
from replayforge.providers.policy import ModelPolicy, load_model_policy
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import (
    ActionableControl,
    ExtractableField,
    NormalizedObservation,
    ScreenRegion,
    Viewport,
    VisualToken,
)
from tests.artifacts import sample_artifact


@pytest.mark.parametrize("bundle", [ProviderTypeLocatorBundle, ProviderExtractLocatorBundle])
def test_visual_form_and_extraction_targets_reject_text_click_candidates(
    bundle: type[ProviderTypeLocatorBundle] | type[ProviderExtractLocatorBundle],
) -> None:
    with pytest.raises(ValidationError):
        bundle.model_validate(
            {
                "description": "A labeled field",
                "visual_candidates": [
                    {
                        "strategy": "ocr_relative",
                        "anchor": "Reference",
                        "target_text": "Value",
                        "relation": "right_of",
                    }
                ],
            }
        )


@dataclass
class FakeResponse:
    output_parsed: ProposalEnvelope | None
    usage: FakeUsage


@dataclass
class FakeUsage:
    input_tokens: int = 120
    output_tokens: int = 30
    total_tokens: int = 150


@dataclass
class FakeResponses:
    result: ProposalEnvelope | None = None
    error: Exception | None = None
    request: dict[str, Any] = field(default_factory=dict)
    usage: FakeUsage = field(default_factory=FakeUsage)

    def parse(self, **kwargs: Any) -> FakeResponse:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        return FakeResponse(self.result, self.usage)


@dataclass
class FakeClient:
    responses: FakeResponses


@dataclass
class FakeTelemetry:
    metrics: list[ModelCallMetric] = field(default_factory=list)

    def ready(self) -> bool:
        return True

    def record(self, metric: ModelCallMetric) -> None:
        self.metrics.append(metric)

    def close(self) -> None:
        return None


@dataclass
class FailingTelemetry(FakeTelemetry):
    def record(self, metric: ModelCallMetric) -> None:
        del metric
        raise RuntimeError("telemetry export failed")


class SafeFakeProviderError(RuntimeError):
    status_code = 400
    code = "invalid_value"


def model_policy(**changes: object) -> ModelPolicy:
    policy = load_model_policy(Path("config/model-policy.yaml"))
    return policy.model_copy(update=changes)


def test_field_direction_is_nullable_and_required_in_strict_provider_schema() -> None:
    schema = to_strict_json_schema(ProviderRenderedFieldValueCandidate)
    assert "relation" in schema["required"]
    assert {item["type"] for item in schema["properties"]["relation"]["anyOf"]} == {
        "string",
        "null",
    }


def test_branch_proposal_is_strict_and_reference_context_contains_no_invocation_values() -> None:
    responses = FakeResponses(
        ProposalEnvelope(
            proposal=ProviderBranchProposal(
                kind="branch",
                condition=ProviderCondition(kind="rendered_text", operand="No matching records"),
                rationale="The live UI reports an empty result",
            )
        )
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy())
    primary = sample_artifact()
    result = provider.decide(
        replace(context(), reference_steps=primary.steps, scenario_kind="business_outcome")
    )
    assert isinstance(result, BranchProposal)
    assert result.condition.kind == "rendered_text"
    sent = json.loads(responses.request["input"][0]["content"][0]["text"])
    assert sent["scenario_kind"] == "business_outcome"
    assert sent["reference_steps"][0]["id"] == primary.steps[0].id
    assert "12345" not in json.dumps(sent)
    schema = to_strict_json_schema(ProviderRecordedActionProposal)
    assert "expected_condition" in schema["required"]
    assert schema["additionalProperties"] is False


def context() -> ProviderContext:
    return ProviderContext(
        goal="Find the savings balance",
        inputs={"member_id": "12345"},
        observation=NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=new_id(EntityKind.SESSION),
            captured_at=datetime(2026, 9, 10, tzinfo=UTC),
            route="/members/search",
            viewport=Viewport(1280, 800),
            fingerprint="state-1",
            landmarks=("Member Search", "Member ID"),
            frame_titles=("Member operations",),
            actionable_controls=(ActionableControl("textbox", "Member ID"),),
            extractable_fields=(ExtractableField("Available balance"),),
        ),
        screenshot_png=b"\x89PNG\r\n\x1a\nsynthetic-frame",
        action_history=("opened search",),
        allowed_action_types=frozenset({"type", "click"}),
        output_contract=sample_artifact().outputs,
        captured_output_names=("member_id",),
        previous_visual_text=("Review restore",),
    )


def test_scenario_prefix_schema_excludes_unmergeable_free_form_actions() -> None:
    responses = FakeResponses(
        ProposalEnvelope(
            proposal=ProviderRecordedActionProposal(
                kind="recorded_action", step_id="lookup", rationale="Repeat observed action"
            )
        )
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(), FakeTelemetry())
    provider.decide(replace(context(), scenario_kind="business_outcome", recorded_step_count=1))
    assert responses.request["text_format"] is ScenarioPrefixEnvelope
    schema = to_strict_json_schema(ScenarioPrefixEnvelope)
    assert "ProviderClickProposal" not in schema["$defs"]
    assert "ProviderBranchProposal" in schema["$defs"]
    assert "ProviderRecordedActionProposal" in schema["$defs"]
    assert "CompleteProposal" not in schema["$defs"]
    provider.decide(replace(context(), scenario_kind="business_outcome"))
    assert responses.request["text_format"] is ScenarioStartEnvelope
    assert "ProviderBranchProposal" not in to_strict_json_schema(ScenarioStartEnvelope)["$defs"]


def test_recovery_schema_and_context_preserve_the_unexecuted_rejoin_boundary() -> None:
    responses = FakeResponses(
        ProposalEnvelope(
            proposal=CompleteProposal(kind="complete", rationale="Correction verified")
        )
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(), FakeTelemetry())
    next_step = sample_artifact().steps[1]
    provider.decide(
        replace(
            context(),
            scenario_kind="recovery",
            branch_observed=True,
            recovery_resume_before=next_step,
        )
    )
    assert responses.request["text_format"] is ScenarioRecoveryEnvelope
    schema = to_strict_json_schema(ScenarioRecoveryEnvelope)
    assert "ProviderRecordedActionProposal" not in schema["$defs"]
    assert "ProviderBranchProposal" not in schema["$defs"]
    sent = json.loads(responses.request["input"][0]["content"][0]["text"])
    assert sent["recovery_resume_before"]["id"] == next_step.id
    assert sent["recovery_resume_before"]["action"] == next_step.action.model_dump(mode="json")
    assert sent["reference_steps"] == []


def test_visual_tokens_expose_exact_symbolic_bindings_not_unseen_values() -> None:
    responses = FakeResponses(
        ProposalEnvelope(proposal=CompleteProposal(kind="complete", rationale="Verified."))
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy())
    original = context()
    tokens = tuple(
        VisualToken(text, 0.99, ScreenRegion(10, 10 + index * 20, 160, 15))
        for index, text in enumerate(
            ("\uff21\uff22-12", "AB-12", "Product AB-12 / Open", "AB-123", "A.*")
        )
    )
    provider.decide(
        replace(
            original,
            inputs={
                "record": {"id": "ab-12"},
                "alias": "AB-12",
                "pattern": "A.*",
                "unseen": "never-visible-sensitive-value",
                "empty": " ",
                "number": 12,
            },
            observation=replace(original.observation, visual_tokens=tokens),
        )
    )
    sent = json.loads(responses.request["input"][0]["content"][0]["text"])
    annotations = [token["input_bindings"] for token in sent["observation"]["visual_tokens"]]
    assert annotations == [["record.id", "alias"], ["record.id", "alias"], [], [], ["pattern"]]
    assert "never-visible-sensitive-value" not in json.dumps(sent)
    assert responses.request["store"] is False


def test_provider_requests_bounded_non_stored_structured_output() -> None:
    responses = FakeResponses(
        ProposalEnvelope(proposal=CompleteProposal(kind="complete", rationale="Verified."))
    )
    telemetry = FakeTelemetry()
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(), telemetry)

    proposal = provider.decide(context())

    assert proposal.kind == "complete"
    assert responses.request["text_format"] is ProposalEnvelope
    assert responses.request["store"] is False
    assert responses.request["tools"] == []
    assert responses.request["model"] == "gpt-5.6-luna"
    assert responses.request["reasoning"] == {"effort": "low"}
    assert responses.request["max_output_tokens"] == 1200
    content = responses.request["input"][0]["content"]
    sent = json.loads(content[0]["text"])
    assert sent["input_fields"] == ["member_id"]
    assert sent["required_output_fields"] == [
        {
            "name": "member_id",
            "type": "string",
            "preferred_transform": "trim",
        },
        {
            "name": "account_type",
            "type": "string",
            "const": "savings",
            "preferred_transform": "lowercase",
        },
        {
            "name": "currency",
            "type": "string",
            "enum": ["USD"],
            "preferred_transform": "trim",
        },
        {
            "name": "available_balance",
            "type": "string",
            "format": "decimal",
            "preferred_transform": "decimal",
        },
        {
            "name": "as_of",
            "type": "string",
            "format": "date-time",
            "preferred_transform": "date-time",
        },
    ]
    assert sent["captured_output_fields"] == ["member_id"]
    assert sent["previous_visual_text"] == ["Review restore"]
    assert sent["remaining_output_fields"] == [
        "account_type",
        "currency",
        "available_balance",
        "as_of",
    ]
    assert sent["observation"]["frame_titles"] == ["Member operations"]
    assert sent["observation"]["actionable_controls"] == [
        {"role": "textbox", "name": "Member ID", "count": 1}
    ]
    assert sent["observation"]["extractable_fields"] == [{"label": "Available balance", "count": 1}]
    assert sent["maximum_risk"] == "read_only"
    assert "12345" not in content[0]["text"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert telemetry.metrics[0].outcome == "success"
    assert telemetry.metrics[0].usage is not None
    assert telemetry.metrics[0].usage.total_tokens == 150


def test_provider_wire_schema_is_minimal_and_uses_supported_union_shape() -> None:
    schema = to_strict_json_schema(ProposalEnvelope)
    serialized = json.dumps(schema, sort_keys=True)

    assert '"oneOf"' not in serialized
    assert '"anyOf"' in serialized
    assert "ProviderAssertAction" in serialized
    assert "ProviderWaitForAction" in serialized
    assert "ProviderNavigateAction" in serialized
    assert '"css"' not in serialized
    assert '"coordinates"' in serialized
    assert '"image_anchor"' not in serialized
    assert '"accessibility_path"' not in serialized
    assert {
        "ProviderClickAction",
        "ProviderTypeAction",
        "ProviderExtractAction",
        "ProviderSelectAction",
        "ProviderPressKeysAction",
        "ProviderScrollAction",
    }.issubset(schema["$defs"])


def test_unknown_input_privacy_does_not_depend_on_field_names() -> None:
    from replayforge.providers.openai import _input_contract

    contract = _input_contract({"employee_id": "A-82", "shipment": "BOX-91", "label": "Private"})
    assert all(
        field.data_classification.value == "personal" for field in contract.properties.values()
    )
    assert all(field.persistence.value == "redacted" for field in contract.properties.values())


def test_provider_plan_requires_text_output_fields() -> None:
    with pytest.raises(ValidationError, match="string"):
        ProviderOutputField(
            name="nested_result",
            type=JsonValueType.OBJECT,  # type: ignore[arg-type]
            description="Nested result",
        )


def test_provider_converts_constrained_wire_locator_to_domain_proposal() -> None:
    responses = FakeResponses(
        ProposalEnvelope(
            proposal=ProviderTypeProposal(
                kind="act",
                action=ProviderTypeAction(
                    kind="type", value=InputValue(source="input", path="member_id")
                ),
                target=ProviderTypeLocatorBundle(
                    description="Member ID field",
                    scope=ProviderLocatorScope(
                        frame_path=(
                            ProviderFrameLocator(
                                locator=ProviderFrameTitleCandidate(
                                    strategy="title", value="Member operations"
                                )
                            ),
                        )
                    ),
                    candidates=(ProviderInputCandidate(strategy="label", value="Member ID"),),
                ),
                rationale="The search field is visible.",
                expected_effect="The member identifier is entered.",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            )
        )
    )

    proposal = OpenAIModelProvider(FakeClient(responses), model_policy()).decide(context())

    assert isinstance(proposal, ActProposal)
    assert proposal.action.kind == "type"
    assert proposal.target is not None
    assert proposal.target.candidates[0].strategy.value == "label"
    assert proposal.target.scope.frame_path[0].locator.value == "Member operations"


def test_provider_relative_target_is_semantic_and_geometry_free() -> None:
    responses = FakeResponses(
        ProposalEnvelope(
            proposal=ProviderClickProposal(
                kind="act",
                action=ProviderClickAction(kind="click"),
                target=ProviderClickLocatorBundle(
                    description="Checking row action",
                    visual_candidates=(
                        ProviderOcrRelativeCandidate(
                            strategy="ocr_relative",
                            anchor="Checking",
                            target_text="Open",
                            relation="same_row",
                        ),
                    ),
                ),
                rationale="The account row is visible.",
                expected_effect="The checking account opens.",
                declared_risk=Risk.READ_ONLY,
                confidence=1,
            )
        )
    )

    proposal = OpenAIModelProvider(FakeClient(responses), model_policy()).decide(context())

    assert isinstance(proposal, ActProposal)
    assert proposal.target is not None
    candidate = proposal.target.visual_candidates[0]
    assert candidate.strategy == "ocr_relative"
    assert candidate.target_text == "Open"
    assert candidate.relative_region is None


def test_provider_rejects_clicking_a_static_following_value() -> None:
    with pytest.raises(ValidationError):
        ProposalEnvelope.model_validate(
            {
                "proposal": {
                    "kind": "act",
                    "action": {"kind": "click"},
                    "target": {
                        "description": "Displayed balance",
                        "scope": {"window": "primary", "frame_path": []},
                        "candidates": [
                            {
                                "strategy": "relative_text",
                                "anchor": "Available balance",
                                "relation": "following_value",
                                "element": "dd",
                                "expected_count": 1,
                            }
                        ],
                    },
                    "rationale": "The value is visible.",
                    "expected_effect": "The value remains visible.",
                    "declared_risk": "read_only",
                    "confidence": 1,
                }
            }
        )


@pytest.mark.parametrize("frame", [b"", b"x" * (5 * 1024 * 1024 + 1)])
def test_provider_rejects_empty_or_oversized_visual_frame(frame: bytes) -> None:
    provider = OpenAIModelProvider(FakeClient(FakeResponses()), model_policy())
    provider_context = context()

    with pytest.raises(ModelProviderError) as captured:
        provider.decide(
            ProviderContext(
                goal=provider_context.goal,
                inputs=provider_context.inputs,
                observation=provider_context.observation,
                screenshot_png=frame,
                action_history=provider_context.action_history,
                allowed_action_types=provider_context.allowed_action_types,
                output_contract=provider_context.output_contract,
                captured_output_names=provider_context.captured_output_names,
            )
        )

    assert captured.value.code == "provider_frame_invalid"


@pytest.mark.parametrize(
    ("responses", "code"),
    [
        (
            FakeResponses(error=SafeFakeProviderError("raw provider failure")),
            "provider_unavailable",
        ),
        (FakeResponses(result=None), "provider_response_invalid"),
    ],
)
def test_provider_errors_are_safe_and_classified(responses: FakeResponses, code: str) -> None:
    telemetry = FakeTelemetry()
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(), telemetry)

    with pytest.raises(ModelProviderError) as captured:
        provider.decide(context())

    assert captured.value.code == code
    assert "raw provider failure" not in captured.value.safe_message
    assert telemetry.metrics[0].outcome == (
        "provider_error" if responses.error is not None else "invalid_response"
    )
    if responses.error is not None:
        assert telemetry.metrics[0].error_category == "request"
        assert telemetry.metrics[0].provider_status_code == 400
        assert telemetry.metrics[0].provider_error_code == "invalid_value"


def test_provider_enforces_per_run_call_budget() -> None:
    responses = FakeResponses(
        ProposalEnvelope(proposal=CompleteProposal(kind="complete", rationale="Verified."))
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(max_model_calls_per_run=1))

    provider.decide(context())

    with pytest.raises(ModelProviderError) as captured:
        provider.decide(context())
    assert captured.value.code == "provider_budget_exceeded"
    assert provider.for_run().decide(context()).kind == "complete"


def test_telemetry_export_failure_does_not_discard_valid_provider_result() -> None:
    responses = FakeResponses(
        ProposalEnvelope(proposal=CompleteProposal(kind="complete", rationale="Verified."))
    )
    provider = OpenAIModelProvider(FakeClient(responses), model_policy(), FailingTelemetry())

    assert provider.decide(context()).kind == "complete"
