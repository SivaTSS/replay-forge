"""OpenAI Responses API adapter for schema-constrained discovery proposals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from replayforge.discovery.models import DiscoveryProposal, ProviderContext
from replayforge.discovery.ports import ModelProviderError

_INSTRUCTIONS = """You select exactly one safe next step for UI workflow discovery.
Return only the provided structured proposal. Use symbolic input paths, never literal customer
values. Prefer semantic locators (role/name, label, relative text), never coordinate-only
targets. Do not navigate to arbitrary URLs. Escalate when state is ambiguous, risky, or stuck.
Declare risk conservatively and complete only when the requested result is visibly verified."""


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


@dataclass(frozen=True, slots=True)
class OpenAIModelProvider:
    client: OpenAIClientPort
    model_name: str
    max_output_tokens: int = 2_000
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("OpenAI model name is required")
        if not 256 <= self.max_output_tokens <= 8_000:
            raise ValueError("provider output budget must be between 256 and 8000 tokens")
        if not 1 <= self.timeout_seconds <= 120:
            raise ValueError("provider timeout must be between 1 and 120 seconds")

    @classmethod
    def from_api_key(cls, api_key: str, model_name: str) -> OpenAIModelProvider:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        return cls(cast(OpenAIClientPort, OpenAI(api_key=api_key, max_retries=1)), model_name)

    @property
    def provider_name(self) -> str:
        return "openai"

    def decide(self, context: ProviderContext) -> DiscoveryProposal:
        request = {
            "goal": context.goal,
            "input_fields": sorted(context.inputs),
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
        try:
            response = self.client.responses.parse(
                model=self.model_name,
                instructions=_INSTRUCTIONS,
                input=json.dumps(request, separators=(",", ":"), ensure_ascii=False),
                text_format=ProposalEnvelope,
                max_output_tokens=self.max_output_tokens,
                store=False,
                tools=[],
                parallel_tool_calls=False,
                timeout=self.timeout_seconds,
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
