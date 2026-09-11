"""OpenAI Responses API adapter for schema-constrained discovery proposals."""

from __future__ import annotations

import json
import time
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from replayforge.discovery.models import DiscoveryProposal, ProviderContext
from replayforge.discovery.ports import ModelProviderError
from replayforge.observability.model_calls import (
    ModelCallMetric,
    ModelCallTelemetry,
    ModelUsage,
    NoOpModelCallTelemetry,
    ProviderErrorCategory,
)
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
        return parsed.proposal

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
