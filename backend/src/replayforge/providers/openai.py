"""OpenAI Responses API adapter for schema-constrained discovery proposals."""

from __future__ import annotations

import json
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from replayforge.discovery.models import DiscoveryProposal, ProviderContext
from replayforge.discovery.ports import ModelProviderError
from replayforge.runtime.model_policy import ModelPolicy

_INSTRUCTIONS = """You select exactly one safe next step for UI workflow discovery.
Return only the provided structured proposal. Use symbolic input paths, never literal customer
values. Prefer semantic locators (role/name, label, relative text), never coordinate-only
targets. Do not navigate to arbitrary URLs. Escalate when state is ambiguous, risky, or stuck.
Declare risk conservatively. Extract every required output using its exact field name, and
complete only when every required output and the requested result are visibly verified."""


class ProposalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal: DiscoveryProposal


class ParsedResponsePort(Protocol):
    @property
    def output_parsed(self) -> ProposalEnvelope | None: ...


class ResponsesPort(Protocol):
    def parse(self, **kwargs: Any) -> ParsedResponsePort: ...


class OpenAIClientPort(Protocol):
    @property
    def responses(self) -> ResponsesPort: ...


@dataclass(slots=True)
class OpenAIModelProvider:
    client: OpenAIClientPort
    policy: ModelPolicy
    _calls_made: int = field(default=0, init=False, repr=False)

    @classmethod
    def from_api_key(cls, api_key: str, policy: ModelPolicy) -> OpenAIModelProvider:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        return cls(cast(OpenAIClientPort, OpenAI(api_key=api_key, max_retries=1)), policy)

    def for_run(self) -> OpenAIModelProvider:
        return OpenAIModelProvider(self.client, self.policy)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.policy.model

    def decide(self, context: ProviderContext) -> DiscoveryProposal:
        if not context.screenshot_png or len(context.screenshot_png) > self.policy.max_frame_bytes:
            raise ModelProviderError(
                "provider_frame_invalid",
                "The visual observation is empty or exceeds the provider frame limit.",
            )
        request = {
            "goal": context.goal,
            "input_fields": sorted(context.inputs),
            "required_output_fields": list(context.required_output_names),
            "observation": {
                "route": context.observation.route,
                "viewport": {
                    "width": context.observation.viewport.width,
                    "height": context.observation.viewport.height,
                },
                "fingerprint": context.observation.fingerprint,
                "landmarks": list(context.observation.landmarks),
                "active_element": context.observation.active_element,
                "dialog_text": context.observation.dialog_text,
            },
            "recent_actions": list(context.action_history[-20:]),
            "allowed_action_types": sorted(context.allowed_action_types),
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
            raise ModelProviderError(
                "provider_unavailable",
                "The model provider could not produce a discovery decision.",
            ) from error
        parsed = response.output_parsed
        if parsed is None:
            raise ModelProviderError(
                "provider_response_invalid",
                "The model provider returned no valid structured discovery decision.",
            )
        return parsed.proposal
