from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from replayforge.api.app import create_app
from replayforge.api.services import ApiServices
from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import CapabilityNotFoundError
from replayforge.capabilities.serialization import dump_artifact_yaml
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.runs.results import (
    CapabilityReference,
    RunResult,
    SuccessResult,
    VerifiedCheckpoint,
)


@dataclass
class FakeReplayInvoker:
    is_ready: bool = True
    calls: list[tuple[str, str | None, str, dict[str, Any]]] | None = None

    def ready(self) -> bool:
        return self.is_ready

    def invoke(
        self,
        capability_id: str,
        version: str | None,
        tenant: str,
        inputs: dict[str, Any],
    ) -> RunResult:
        if self.calls is not None:
            self.calls.append((capability_id, version, tenant, inputs))
        return SuccessResult(
            status="success",
            run_id="run_0123456789abcdef0123456789abcdef",
            capability=CapabilityReference(id=capability_id, version=version or "1.0.0"),
            outputs={"available_balance": "1420.57"},
            checkpoint=VerifiedCheckpoint(id="savings_balance_verified", verified=True),
            evidence_manifest="evidence://test/manifest.json",
        )


@dataclass
class FakeDiscoveryInvoker:
    artifact: CapabilityArtifact
    is_ready: bool = True

    def ready(self) -> bool:
        return self.is_ready

    def invoke(self, **kwargs: Any) -> DiscoveryResult:
        return DiscoverySuccess(
            status="success",
            run_id="run_0123456789abcdef0123456789abcdef",
            artifact=self.artifact,
            evidence_manifest="evidence://test/manifest.json",
        )


def client(invoker: FakeReplayInvoker | None = None) -> TestClient:
    return TestClient(create_app(ApiServices(invoker or FakeReplayInvoker())))


def test_health_and_correlation_contract() -> None:
    response = client().get("/health/live", headers={"x-correlation-id": "trace.client-123"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-correlation-id"] == "trace.client-123"


def test_invalid_correlation_id_is_replaced() -> None:
    response = client().get("/health/live", headers={"x-correlation-id": "contains unsafe spaces"})

    assert response.headers["x-correlation-id"].startswith("trc_")


def test_readiness_failure_is_structured_and_retryable() -> None:
    response = client(FakeReplayInvoker(is_ready=False)).get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "runtime_not_ready"
    assert body["retryable"] is True
    assert "correlation_id" in body


def test_machine_schema_endpoint_returns_versioned_contract() -> None:
    response = client().get("/api/v1/capabilities/schema")

    assert response.status_code == 200
    assert response.json()["properties"]["schema_version"]["const"] == "1.0"


def test_artifact_validation_returns_identity_and_hash(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    response = client().post(
        "/api/v1/capabilities/validate", json={"yaml": dump_artifact_yaml(artifact)}
    )

    assert response.status_code == 200
    assert response.json()["capability_id"] == artifact.capability.id
    assert response.json()["content_hash"].startswith("sha256:")


def test_invalid_artifact_error_does_not_echo_payload() -> None:
    sensitive_marker = "do-not-echo-this-value"

    response = client().post("/api/v1/capabilities/validate", json={"yaml": sensitive_marker})

    assert response.status_code == 422
    assert response.json()["code"] == "artifact_invalid"
    assert sensitive_marker not in response.text


def test_replay_and_agent_invocation_share_typed_contract() -> None:
    calls: list[tuple[str, str | None, str, dict[str, Any]]] = []
    invoker = FakeReplayInvoker(calls=calls)
    api = client(invoker)
    payload = {
        "tenant": "harbor",
        "version": "1.0.0",
        "inputs": {"member_id": "12345"},
    }

    replay = api.post("/api/v1/capabilities/member.lookup_savings_balance/replays", json=payload)
    invocation = api.post("/api/v1/capabilities/member.lookup_savings_balance/invoke", json=payload)

    assert replay.status_code == invocation.status_code == 200
    assert replay.json()["status"] == invocation.json()["status"] == "success"
    assert len(calls) == 2


def test_request_validation_errors_exclude_input_values() -> None:
    sensitive_marker = "do-not-echo-member-value"

    response = client().post(
        "/api/v1/capabilities/member.lookup/replays",
        json={"tenant": "INVALID TENANT", "inputs": {"member_id": sensitive_marker}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"
    assert sensitive_marker not in response.text
    assert all("input" not in detail for detail in response.json()["details"])


def test_unknown_capability_is_a_sanitized_404() -> None:
    class MissingInvoker(FakeReplayInvoker):
        def invoke(
            self,
            capability_id: str,
            version: str | None,
            tenant: str,
            inputs: dict[str, Any],
        ) -> RunResult:
            raise CapabilityNotFoundError(f"internal lookup: {capability_id}/{version}")

    api = client(MissingInvoker())

    response = api.post(
        "/api/v1/capabilities/member.missing/replays",
        json={"version": "1.0.0", "tenant": "harbor_credit_union", "inputs": {}},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "capability_not_found"
    assert "internal lookup" not in response.text


def test_discovery_requires_provider_readiness() -> None:
    response = client().post(
        "/api/v1/discoveries",
        json={
            "goal": "Look up the current savings balance",
            "application_family": "northstar_member_service",
            "tenant": "harbor",
            "entry_point": "member_search",
            "inputs": {"member_id": "12345"},
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "discovery_not_ready"


def test_successful_discovery_returns_compiled_artifact(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    api = TestClient(create_app(ApiServices(FakeReplayInvoker(), FakeDiscoveryInvoker(artifact))))

    response = api.post(
        "/api/v1/discoveries",
        json={
            "goal": "Look up the current savings balance",
            "application_family": "northstar_member_service",
            "tenant": "harbor_credit_union",
            "entry_point": "member_search",
            "inputs": {"member_id": "12345"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["artifact"]["capability"]["id"] == artifact.capability.id
