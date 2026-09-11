from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.discovery.models import CompleteProposal, ProviderContext
from replayforge.discovery.ports import ModelProviderError
from replayforge.providers.openai import OpenAIModelProvider, ProposalEnvelope
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


@dataclass
class FakeResponse:
    output_parsed: ProposalEnvelope | None


@dataclass
class FakeResponses:
    result: ProposalEnvelope | None = None
    error: Exception | None = None
    request: dict[str, Any] = field(default_factory=dict)

    def parse(self, **kwargs: Any) -> FakeResponse:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        return FakeResponse(self.result)


@dataclass
class FakeClient:
    responses: FakeResponses


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
    provider = OpenAIModelProvider(FakeClient(responses), "gpt-test")

    proposal = provider.decide(context())

    assert proposal.kind == "complete"
    assert responses.request["text_format"] is ProposalEnvelope
    assert responses.request["store"] is False
    assert responses.request["tools"] == []
    assert responses.request["max_output_tokens"] == 2_000
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
    assert "12345" not in content[0]["text"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("frame", [b"", b"x" * (5 * 1024 * 1024 + 1)])
def test_provider_rejects_empty_or_oversized_visual_frame(frame: bytes) -> None:
    provider = OpenAIModelProvider(FakeClient(FakeResponses()), "gpt-test")
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
        (FakeResponses(error=RuntimeError("raw provider failure")), "provider_unavailable"),
        (FakeResponses(result=None), "provider_response_invalid"),
    ],
)
def test_provider_errors_are_safe_and_classified(responses: FakeResponses, code: str) -> None:
    provider = OpenAIModelProvider(FakeClient(responses), "gpt-test")

    with pytest.raises(ModelProviderError) as captured:
        provider.decide(context())

    assert captured.value.code == code
    assert "raw provider failure" not in captured.value.safe_message


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_name": ""}, "model name"),
        ({"model_name": "gpt-test", "max_output_tokens": 10}, "output budget"),
        ({"model_name": "gpt-test", "timeout_seconds": 0}, "timeout"),
    ],
)
def test_provider_configuration_is_bounded(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OpenAIModelProvider(FakeClient(FakeResponses()), **kwargs)
