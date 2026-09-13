from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from replayforge.api.app import create_app
from replayforge.api.services import ApiServices, DiscoverySuiteInvoker
from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.registry import CapabilityNotFoundError
from replayforge.capabilities.serialization import dump_artifact_yaml
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.interventions.leases import LeaseConflictError, LeaseExpiredError
from replayforge.interventions.models import (
    ControlLease,
    ControlOwner,
    HumanInputCommand,
    HumanInputReceipt,
    Intervention,
    InterventionFrame,
    InterventionStatus,
    OwnerKind,
)
from replayforge.interventions.router import InterventionNotFoundError
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionResume,
    InterventionTransition,
)
from replayforge.runs.results import (
    CapabilityReference,
    RunResult,
    SuccessResult,
    VerifiedCheckpoint,
)
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import Viewport


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
            evidence_manifest=("evidence://run_0123456789abcdef0123456789abcdef/manifest.json"),
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


@dataclass
class FakeDiscoverySuiteInvoker:
    artifact: CapabilityArtifact

    def ready(self) -> bool:
        return True

    def published_artifact(self, suite_id: str) -> CapabilityArtifact:
        assert suite_id == "run_0123456789abcdef0123456789abcdef"
        return self.artifact


@dataclass
class FakeInterventionInvoker:
    transition: InterventionTransition
    frame: bytes = b"\x89PNG\r\n\x1a\nframe"
    resume_result: RunResult | None = None

    def get(self, intervention_id: str) -> InterventionTransition:
        return self.transition

    def list_active(self, run_mode: object = None) -> tuple[InterventionTransition, ...]:
        return (self.transition,)

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.transition

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.transition

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionResume:
        return InterventionResume(self.transition, self.resume_result)

    def viewport(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionFrame:
        return InterventionFrame(self.frame, 17, Viewport(1280, 800), 9)

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.transition

    def send_input(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str,
        command: HumanInputCommand,
    ) -> HumanInputReceipt:
        return HumanInputReceipt(command.client_sequence, command.source_frame_sequence)

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition:
        return self.transition


def intervention_transition() -> InterventionTransition:
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    session_id = new_id(EntityKind.SESSION)
    intervention_id = new_id(EntityKind.INTERVENTION)
    return InterventionTransition(
        Intervention(
            id=intervention_id,
            run_id=new_id(EntityKind.RUN),
            session_id=session_id,
            trigger_code="unexpected_dialog",
            explanation="Automation paused safely.",
            status=InterventionStatus.CLAIMED,
            created_at=now,
            operator_id="operator-7",
        ),
        ControlLease(
            session_id=session_id,
            owner=ControlOwner(OwnerKind.HUMAN, "operator-7"),
            version=3,
            issued_at=now,
            last_heartbeat=now,
            expires_at=now + timedelta(seconds=30),
            intervention_id=intervention_id,
        ),
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
    assert response.json()["properties"]["schema_version"]["enum"] == [
        "1.0",
        "1.1",
        "1.2",
        "1.3",
        "1.4",
    ]


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


def test_published_discovery_suite_artifact_is_downloadable(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    app = create_app(
        ApiServices(
            FakeReplayInvoker(),
            discovery_suite_invoker=cast(
                DiscoverySuiteInvoker, FakeDiscoverySuiteInvoker(artifact)
            ),
        )
    )

    response = TestClient(app).get(
        "/api/v1/discovery-suites/run_0123456789abcdef0123456789abcdef/artifact"
    )

    assert response.status_code == 200
    assert response.json()["capability"]["id"] == artifact.capability.id


def test_discovery_suite_approval_endpoint_does_not_exist() -> None:
    response = client().post(
        "/api/v1/discovery-suites/run_0123456789abcdef0123456789abcdef/approve",
        json={"operator_id": "operator-1", "expected_hash": f"sha256:{'0' * 64}"},
    )

    assert response.status_code == 404


def test_intervention_claim_returns_new_owner_and_lease_version() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/claim",
        json={"expected_lease_version": 2, "operator_id": "operator-7"},
    )

    assert response.status_code == 200
    assert response.json()["control_owner"] == "human:operator-7"
    assert response.json()["lease_version"] == 3


def test_intervention_inbox_returns_active_transition_context() -> None:
    transition = intervention_transition()
    response = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    ).get("/api/v1/interventions", params={"run_mode": "replay"})

    assert response.status_code == 200
    assert response.json()["items"][0]["intervention_id"] == str(transition.intervention.id)
    assert response.json()["items"][0]["trigger_code"] == "unexpected_dialog"


def test_intervention_viewport_is_non_cacheable_png() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )

    response = api.get(
        f"/api/v1/interventions/{transition.intervention.id}/viewport",
        params={"expected_lease_version": 3, "operator_id": "operator-7"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-replayforge-frame-sequence"] == "17"
    assert response.headers["x-replayforge-viewport-width"] == "1280"
    assert response.headers["x-replayforge-viewport-height"] == "800"
    assert response.headers["x-replayforge-next-client-sequence"] == "9"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_intervention_heartbeat_returns_rotated_lease() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/heartbeat",
        json={"expected_lease_version": 2, "operator_id": "operator-7"},
    )

    assert response.status_code == 200
    assert response.json()["lease_version"] == transition.lease.version


def test_intervention_resume_returns_typed_terminal_result() -> None:
    transition = intervention_transition()
    result = SuccessResult(
        status="success",
        run_id=str(transition.intervention.run_id),
        capability=CapabilityReference(id="member.lookup", version="1.0.0"),
        outputs={"balance": "1420.57"},
        checkpoint=VerifiedCheckpoint(id="balance_verified", verified=True),
        evidence_manifest=f"evidence://{transition.intervention.run_id}/manifest.json",
    )
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition, resume_result=result),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/resume",
        json={"expected_lease_version": 3, "operator_id": "operator-7"},
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "success"
    assert response.json()["result"]["checkpoint"]["verified"] is True


def test_intervention_input_accepts_frame_bound_pointer_command() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/input",
        json={
            "expected_lease_version": 3,
            "operator_id": "operator-7",
            "client_sequence": 9,
            "source_frame_sequence": 17,
            "viewport_width": 1280,
            "viewport_height": 800,
            "input": {"kind": "pointer", "x": 320, "y": 240},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "client_sequence": 9,
        "source_frame_sequence": 17,
    }


def test_intervention_input_validation_does_not_echo_text() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )
    sensitive_marker = "never-echo-this-human-input"

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/input",
        json={
            "expected_lease_version": 3,
            "operator_id": "operator-7",
            "client_sequence": 1,
            "source_frame_sequence": 17,
            "viewport_width": 1280,
            "viewport_height": 800,
            "input": {"kind": "text", "text": sensitive_marker, "unexpected": True},
        },
    )

    assert response.status_code == 422
    assert sensitive_marker not in response.text


def test_intervention_input_rejects_pointer_outside_declared_viewport() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/input",
        json={
            "expected_lease_version": 3,
            "operator_id": "operator-7",
            "client_sequence": 1,
            "source_frame_sequence": 17,
            "viewport_width": 1280,
            "viewport_height": 800,
            "input": {"kind": "pointer", "x": 1280, "y": 200},
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"


def test_intervention_runtime_absence_is_retryable() -> None:
    response = client().get("/api/v1/interventions/int_0123456789abcdef0123456789abcdef")

    assert response.status_code == 503
    assert response.json()["code"] == "intervention_runtime_unavailable"
    assert response.json()["retryable"] is True


def test_stale_intervention_error_is_sanitized() -> None:
    class ConflictingInvoker(FakeInterventionInvoker):
        def claim(
            self, intervention_id: str, expected_lease_version: int, operator_id: str
        ) -> InterventionTransition:
            raise LeaseConflictError("internal current owner details")

    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=ConflictingInvoker(transition),
            )
        )
    )

    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/claim",
        json={"expected_lease_version": 2, "operator_id": "operator-7"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "intervention_transition_conflict"
    assert "internal current owner" not in response.text


@pytest.mark.parametrize(
    ("raised", "status", "code"),
    [
        (InterventionAuthorizationError("private owner"), 403, "intervention_forbidden"),
        (LeaseExpiredError("private expiry"), 409, "control_lease_expired"),
    ],
)
def test_owner_and_expiry_failures_have_actionable_public_codes(
    raised: Exception, status: int, code: str
) -> None:
    class RejectingInvoker(FakeInterventionInvoker):
        def heartbeat(
            self, intervention_id: str, expected_lease_version: int, operator_id: str
        ) -> InterventionTransition:
            raise raised

    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=RejectingInvoker(transition),
            )
        )
    )
    response = api.post(
        f"/api/v1/interventions/{transition.intervention.id}/heartbeat",
        json={"expected_lease_version": 3, "operator_id": "operator-7"},
    )

    assert response.status_code == status
    assert response.json()["code"] == code
    assert "private" not in response.text


def test_intervention_read_release_resume_and_terminate_contracts() -> None:
    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=FakeInterventionInvoker(transition),
            )
        )
    )
    base = f"/api/v1/interventions/{transition.intervention.id}"

    responses = [
        api.get(base),
        api.post(
            f"{base}/release",
            json={"expected_lease_version": 3, "operator_id": "operator-7"},
        ),
        api.post(
            f"{base}/resume",
            json={"expected_lease_version": 3, "operator_id": "operator-7"},
        ),
        api.post(
            f"{base}/terminate",
            json={
                "expected_lease_version": 3,
                "operator_id": "operator-7",
                "resolution": "Operator ended the run.",
            },
        ),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(
        response.json()["intervention_id"] == str(transition.intervention.id)
        for response in responses
    )


def test_unknown_intervention_error_is_sanitized() -> None:
    class MissingInterventionInvoker(FakeInterventionInvoker):
        def get(self, intervention_id: str) -> InterventionTransition:
            raise InterventionNotFoundError("internal intervention lookup")

    transition = intervention_transition()
    api = TestClient(
        create_app(
            ApiServices(
                FakeReplayInvoker(),
                intervention_invoker=MissingInterventionInvoker(transition),
            )
        )
    )

    response = api.get(f"/api/v1/interventions/{transition.intervention.id}")

    assert response.status_code == 404
    assert response.json()["code"] == "intervention_not_found"
    assert "internal intervention lookup" not in response.text
