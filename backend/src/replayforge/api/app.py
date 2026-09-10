"""Thin FastAPI adapter with sanitized, correlation-aware errors."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from replayforge.api.contracts import (
    ArtifactValidationRequest,
    ArtifactValidationResponse,
    DiscoveryInvocation,
    ErrorBody,
    HealthResponse,
    InterventionTransitionResponse,
    LeaseTransitionRequest,
    ReplayInvocation,
    TerminateInterventionRequest,
)
from replayforge.api.services import ApiServices
from replayforge.capabilities.registry import CapabilityNotFoundError
from replayforge.capabilities.serialization import (
    ArtifactParseError,
    artifact_content_hash,
    artifact_json_schema,
    load_artifact_yaml,
)
from replayforge.discovery.models import DiscoverySuccess
from replayforge.interventions.leases import LeaseConflictError, LeaseNotFoundError
from replayforge.interventions.models import InterventionTransitionError
from replayforge.interventions.router import (
    InterventionConflictError,
    InterventionNotFoundError,
)
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionTransition,
)
from replayforge.shared.ids import EntityKind, new_id

_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{8,100}$")


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(
        title="ReplayForge Runtime API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("x-correlation-id", "")
        correlation_id = (
            supplied if _CORRELATION_PATTERN.fullmatch(supplied) else new_id(EntityKind.TRACE)
        )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation_id
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"location": ".".join(str(item) for item in issue["loc"]), "type": issue["type"]}
            for issue in error.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="request_validation_failed",
            message="The request does not satisfy the API contract.",
            details=details,
        )

    @app.exception_handler(CapabilityNotFoundError)
    async def capability_not_found(
        request: Request, error: CapabilityNotFoundError
    ) -> JSONResponse:
        del error
        return _error_response(
            request,
            status_code=404,
            code="capability_not_found",
            message="The requested capability or version was not found.",
        )

    for not_found_error in (InterventionNotFoundError, LeaseNotFoundError):
        app.add_exception_handler(not_found_error, _intervention_not_found)
    for conflict_error in (
        LeaseConflictError,
        InterventionConflictError,
        InterventionTransitionError,
        InterventionAuthorizationError,
    ):
        app.add_exception_handler(conflict_error, _intervention_conflict)

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready(request: Request) -> HealthResponse | JSONResponse:
        if not services.replay_invoker.ready():
            return _error_response(
                request,
                status_code=503,
                code="runtime_not_ready",
                message="One or more required runtime dependencies are unavailable.",
                retryable=True,
            )
        return HealthResponse(status="ready")

    @app.post("/api/v1/discoveries")
    def discover(request: Request, body: DiscoveryInvocation) -> JSONResponse:
        if services.discovery_invoker is None or not services.discovery_invoker.ready():
            return _error_response(
                request,
                status_code=503,
                code="discovery_not_ready",
                message="The configured model provider is unavailable.",
                retryable=True,
            )
        result = services.discovery_invoker.invoke(**body.model_dump())
        if isinstance(result, DiscoverySuccess):
            payload = {
                "status": result.status,
                "run_id": result.run_id,
                "artifact": result.artifact.model_dump(mode="json"),
                "evidence_manifest": result.evidence_manifest,
            }
            return JSONResponse(payload, status_code=200)
        status_code = 202 if result.status == "intervention_required" else 200
        return JSONResponse(result.model_dump(mode="json"), status_code=status_code)

    @app.get("/api/v1/capabilities/schema")
    def capability_schema() -> dict[str, Any]:
        return artifact_json_schema()

    @app.post("/api/v1/capabilities/validate", response_model=ArtifactValidationResponse)
    def validate_artifact(
        request: Request, body: ArtifactValidationRequest
    ) -> ArtifactValidationResponse | JSONResponse:
        try:
            artifact = load_artifact_yaml(body.yaml)
        except (ArtifactParseError, ValidationError):
            return _error_response(
                request,
                status_code=422,
                code="artifact_invalid",
                message="The artifact failed syntax or semantic validation.",
            )
        return ArtifactValidationResponse(
            valid=True,
            capability_id=artifact.capability.id,
            version=artifact.capability.version,
            content_hash=artifact_content_hash(artifact),
        )

    @app.post("/api/v1/capabilities/{capability_id}/replays")
    def replay(capability_id: str, body: ReplayInvocation) -> JSONResponse:
        result = services.replay_invoker.invoke(
            capability_id, body.version, body.tenant, body.inputs
        )
        status_code = 202 if result.status == "intervention_required" else 200
        return JSONResponse(result.model_dump(mode="json"), status_code=status_code)

    @app.post("/api/v1/capabilities/{capability_id}/invoke")
    def invoke(capability_id: str, body: ReplayInvocation) -> JSONResponse:
        return replay(capability_id, body)

    @app.get(
        "/api/v1/interventions/{intervention_id}",
        response_model=InterventionTransitionResponse,
    )
    def get_intervention(
        request: Request, intervention_id: str
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(invoker.get(intervention_id))

    @app.post(
        "/api/v1/interventions/{intervention_id}/claim",
        response_model=InterventionTransitionResponse,
    )
    def claim_intervention(
        request: Request, intervention_id: str, body: LeaseTransitionRequest
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(
            invoker.claim(intervention_id, body.expected_lease_version, body.operator_id)
        )

    @app.post(
        "/api/v1/interventions/{intervention_id}/release",
        response_model=InterventionTransitionResponse,
    )
    def release_intervention(
        request: Request, intervention_id: str, body: LeaseTransitionRequest
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(
            invoker.release(intervention_id, body.expected_lease_version, body.operator_id)
        )

    @app.post(
        "/api/v1/interventions/{intervention_id}/resume",
        response_model=InterventionTransitionResponse,
    )
    def resume_intervention(
        request: Request, intervention_id: str, body: LeaseTransitionRequest
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(
            invoker.begin_resume(intervention_id, body.expected_lease_version, body.operator_id)
        )

    @app.get(
        "/api/v1/interventions/{intervention_id}/viewport",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def intervention_viewport(
        request: Request,
        intervention_id: str,
        expected_lease_version: Annotated[int, Query(ge=1)],
        operator_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.@-]{2,100}$")],
    ) -> Response:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        frame = invoker.viewport(intervention_id, expected_lease_version, operator_id)
        return Response(
            frame.content,
            media_type="image/png",
            headers={
                "cache-control": "no-store, max-age=0",
                "content-security-policy": "default-src 'none'; sandbox",
                "x-content-type-options": "nosniff",
                "x-replayforge-frame-sequence": str(frame.sequence),
                "x-replayforge-viewport-width": str(frame.viewport.width),
                "x-replayforge-viewport-height": str(frame.viewport.height),
            },
        )

    @app.post(
        "/api/v1/interventions/{intervention_id}/heartbeat",
        response_model=InterventionTransitionResponse,
    )
    def heartbeat_intervention(
        request: Request, intervention_id: str, body: LeaseTransitionRequest
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(
            invoker.heartbeat(intervention_id, body.expected_lease_version, body.operator_id)
        )

    @app.post(
        "/api/v1/interventions/{intervention_id}/terminate",
        response_model=InterventionTransitionResponse,
    )
    def terminate_intervention(
        request: Request, intervention_id: str, body: TerminateInterventionRequest
    ) -> InterventionTransitionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return _transition_response(
            invoker.terminate(
                intervention_id,
                body.expected_lease_version,
                body.operator_id,
                body.resolution,
            )
        )

    return app


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        code=code,
        message=message,
        correlation_id=str(request.state.correlation_id),
        retryable=retryable,
        details=details or [],
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


async def _intervention_not_found(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=404,
        code="intervention_not_found",
        message="The requested intervention was not found.",
    )


async def _intervention_conflict(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=409,
        code="intervention_transition_conflict",
        message="The intervention state or control lease is stale.",
    )


def _intervention_invoker(request: Request, services: ApiServices) -> Any:
    if services.intervention_invoker is not None:
        return services.intervention_invoker
    return _error_response(
        request,
        status_code=503,
        code="intervention_runtime_unavailable",
        message="The intervention runtime is unavailable.",
        retryable=True,
    )


def _transition_response(
    transition: InterventionTransition,
) -> InterventionTransitionResponse:
    return InterventionTransitionResponse(
        intervention_id=str(transition.intervention.id),
        run_id=str(transition.intervention.run_id),
        session_id=str(transition.intervention.session_id),
        status=transition.intervention.status.value,
        control_owner=transition.lease.owner.value,
        lease_version=transition.lease.version,
        lease_expires_at=transition.lease.expires_at.isoformat().replace("+00:00", "Z"),
    )
