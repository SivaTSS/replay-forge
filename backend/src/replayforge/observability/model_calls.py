"""Privacy-preserving model-call monitoring with an optional local Langfuse adapter."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from langfuse import Langfuse

from replayforge.runtime.model_policy import ModelPolicy

logger = logging.getLogger(__name__)

ProviderErrorCategory = Literal[
    "authentication",
    "permission",
    "rate_limit",
    "timeout",
    "connection",
    "request",
    "server",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ModelCallMetric:
    call_index: int
    latency_ms: int
    outcome: Literal["success", "provider_error", "invalid_response"]
    usage: ModelUsage | None = None
    error_category: ProviderErrorCategory | None = None
    provider_status_code: int | None = None
    provider_error_code: str | None = None


class ModelCallTelemetry(Protocol):
    def ready(self) -> bool: ...

    def record(self, metric: ModelCallMetric) -> None: ...

    def close(self) -> None: ...


class LangfuseClientPort(Protocol):
    def auth_check(self) -> bool: ...

    def start_as_current_observation(self, **kwargs: Any) -> Any: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class NoOpModelCallTelemetry:
    def ready(self) -> bool:
        return False

    def record(self, metric: ModelCallMetric) -> None:
        del metric

    def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class LangfuseModelCallTelemetry:
    client: LangfuseClientPort
    policy: ModelPolicy

    @classmethod
    def create(
        cls,
        *,
        public_key: str,
        secret_key: str,
        base_url: str,
        policy: ModelPolicy,
    ) -> LangfuseModelCallTelemetry:
        if not public_key or not secret_key:
            raise ValueError("Langfuse client keys are required")
        client = cast(
            LangfuseClientPort,
            Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=base_url,
                environment="local",
                flush_at=1,
                flush_interval=1,
                tracing_enabled=True,
            ),
        )
        return cls(client, policy)

    def ready(self) -> bool:
        try:
            return self.client.auth_check()
        except Exception:
            logger.warning("local Langfuse readiness check failed")
            return False

    def record(self, metric: ModelCallMetric) -> None:
        usage_details: dict[str, int] | None = None
        cost_details: dict[str, float] | None = None
        if metric.usage is not None:
            usage_details = {
                "input": metric.usage.input_tokens,
                "output": metric.usage.output_tokens,
                "total": metric.usage.total_tokens,
            }
            unit = self.policy.pricing.unit_tokens
            input_cost = self.policy.pricing.input_per_unit * metric.usage.input_tokens / unit
            output_cost = self.policy.pricing.output_per_unit * metric.usage.output_tokens / unit
            cost_details = {
                "input": float(input_cost),
                "output": float(output_cost),
                "total": float(input_cost + output_cost),
            }
        metadata: dict[str, int | str] = {
            "call_index": metric.call_index,
            "latency_ms": metric.latency_ms,
            "outcome": metric.outcome,
            "payload_capture": "disabled",
        }
        if metric.error_category is not None:
            metadata["error_category"] = metric.error_category
        if metric.provider_status_code is not None and 100 <= metric.provider_status_code <= 599:
            metadata["provider_status_code"] = metric.provider_status_code
        if metric.provider_error_code is not None and re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", metric.provider_error_code
        ):
            metadata["provider_error_code"] = metric.provider_error_code
        try:
            with self.client.start_as_current_observation(
                as_type="generation",
                name="replayforge.discovery.decision",
                model=self.policy.model,
                model_parameters={
                    "max_output_tokens": self.policy.max_output_tokens,
                    "reasoning_effort": self.policy.reasoning_effort,
                },
                metadata=metadata,
                usage_details=usage_details,
                cost_details=cost_details,
            ):
                pass
        except Exception:
            logger.warning("local Langfuse model-call export failed")

    def close(self) -> None:
        try:
            self.client.flush()
            self.client.shutdown()
        except Exception:
            logger.warning("local Langfuse shutdown failed")
