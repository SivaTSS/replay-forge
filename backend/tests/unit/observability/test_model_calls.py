from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from replayforge.observability.model_calls import (
    LangfuseModelCallTelemetry,
    ModelCallMetric,
    ModelUsage,
)
from replayforge.runtime.model_policy import load_model_policy


@dataclass
class FakeLangfuseClient:
    authenticated: bool = True
    fail_auth: bool = False
    fail_export: bool = False
    observations: list[dict[str, Any]] = field(default_factory=list)
    flush_count: int = 0

    def auth_check(self) -> bool:
        if self.fail_auth:
            raise RuntimeError("secret auth diagnostics")
        return self.authenticated

    def start_as_current_observation(self, **kwargs: Any) -> Any:
        if self.fail_export:
            raise RuntimeError("secret export diagnostics")
        self.observations.append(kwargs)
        return nullcontext()

    def flush(self) -> None:
        self.flush_count += 1


def telemetry(client: FakeLangfuseClient) -> LangfuseModelCallTelemetry:
    return LangfuseModelCallTelemetry(
        client,
        load_model_policy(Path("config/model-policy.yaml")),
    )


def test_local_telemetry_exports_only_safe_usage_cost_and_operational_metadata() -> None:
    client = FakeLangfuseClient()
    monitor = telemetry(client)

    monitor.record(
        ModelCallMetric(
            call_index=2,
            latency_ms=145,
            outcome="success",
            usage=ModelUsage(input_tokens=1_000, output_tokens=200, total_tokens=1_200),
        )
    )

    assert client.observations == [
        {
            "as_type": "generation",
            "name": "replayforge.discovery.decision",
            "model": "gpt-5.6-luna",
            "model_parameters": {"max_output_tokens": 600, "reasoning_effort": "low"},
            "metadata": {
                "call_index": 2,
                "latency_ms": 145,
                "outcome": "success",
                "payload_capture": "disabled",
            },
            "usage_details": {"input": 1_000, "output": 200, "total": 1_200},
            "cost_details": {"input": 0.0002, "output": 0.00024, "total": 0.00044},
        }
    ]
    exported_observation = client.observations[0]
    assert "input" not in exported_observation
    assert "output" not in exported_observation
    assert "screenshot" not in repr(exported_observation)
    assert "customer" not in repr(exported_observation)


def test_local_telemetry_is_fail_closed_for_readiness_and_fail_open_after_call() -> None:
    unavailable = FakeLangfuseClient(authenticated=False)
    broken_auth = FakeLangfuseClient(fail_auth=True)
    broken_export = FakeLangfuseClient(fail_export=True)

    assert not telemetry(unavailable).ready()
    assert not telemetry(broken_auth).ready()
    telemetry(broken_export).record(ModelCallMetric(1, 10, "provider_error"))


def test_local_telemetry_exports_only_bounded_provider_failure_details() -> None:
    client = FakeLangfuseClient()

    telemetry(client).record(
        ModelCallMetric(
            1,
            24,
            "provider_error",
            error_category="request",
            provider_status_code=400,
            provider_error_code="invalid_value",
        )
    )

    assert client.observations[0]["metadata"] == {
        "call_index": 1,
        "latency_ms": 24,
        "outcome": "provider_error",
        "payload_capture": "disabled",
        "error_category": "request",
        "provider_status_code": 400,
        "provider_error_code": "invalid_value",
    }


def test_local_telemetry_flushes_without_terminating_process_global_resources() -> None:
    client = FakeLangfuseClient()

    telemetry(client).close()

    assert client.flush_count == 1
