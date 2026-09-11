from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema

from replayforge.discovery.models import CompleteProposal, ProviderContext
from replayforge.discovery.ports import ModelProviderError
from replayforge.observability.model_calls import ModelCallMetric
from replayforge.providers.openai import OpenAIModelProvider, ProposalEnvelope
from replayforge.runtime.model_policy import ModelPolicy, load_model_policy
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


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
        ),
        screenshot_png=b"\x89PNG\r\n\x1a\nsynthetic-frame",
        action_history=("opened search",),
        allowed_action_types=frozenset({"type", "click"}),
        required_output_names=(
            "member_id",
            "account_type",
            "currency",
            "available_balance",
            "as_of",
        ),
    )


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
    assert responses.request["max_output_tokens"] == 600
    content = responses.request["input"][0]["content"]
    sent = json.loads(content[0]["text"])
    assert sent["input_fields"] == ["member_id"]
    assert sent["required_output_fields"] == [
        "member_id",
        "account_type",
        "currency",
        "available_balance",
        "as_of",
    ]
    assert sent["observation"]["frame_titles"] == ["Member operations"]
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
    assert "AssertAction" not in serialized
    assert "WaitForAction" not in serialized
    assert "NavigateAction" not in serialized
    assert {
        "ProviderClickAction",
        "ProviderTypeAction",
        "ProviderExtractAction",
    }.issubset(schema["$defs"])


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
                required_output_names=provider_context.required_output_names,
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
