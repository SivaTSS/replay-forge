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
    DiscoverySuiteScenario,
    DiscoverySuiteValidation,
    ErrorBody,
    HealthResponse,
    HumanInputRequest,
    HumanInputResponse,
    InterventionListResponse,
    InterventionTransitionResponse,
    KeyInputPayload,
    LeaseTransitionRequest,
    ReplayInvocation,
    ResumeInterventionResponse,
    TerminateInterventionRequest,
    TextInputPayload,
)
from replayforge.api.services import ApiServices
from replayforge.api.viewing import viewing_router
from replayforge.capabilities.registry import CapabilityNotFoundError
from replayforge.capabilities.serialization import (
    ArtifactParseError,
    artifact_content_hash,
    artifact_json_schema,
    load_artifact_yaml,
)
from replayforge.discovery.models import DiscoverySuccess
from replayforge.interventions.leases import (
    LeaseConflictError,
    LeaseExpiredError,
    LeaseNotFoundError,
)
from replayforge.interventions.models import (
    HumanInputCommand,
    HumanInputConflictError,
    InterventionRunMode,
    InterventionTransitionError,
)
from replayforge.interventions.router import (
    InterventionConflictError,
    InterventionNotFoundError,
)
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionTransition,
)
from replayforge.runs.discovery_suite import DiscoverySuiteError
from replayforge.runs.viewing import ViewingError
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import (
    HumanInput,
    HumanKey,
    HumanKeyInput,
    HumanPointerInput,
    HumanTextInput,
    Viewport,
)

_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{8,100}$")


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(
        title="ReplayForge Runtime API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    if services.execution_controller is not None:
        app.include_router(viewing_router(services.execution_controller))

    @app.exception_handler(ViewingError)
    async def viewing_error(request: Request, error: ViewingError) -> JSONResponse:
        del request
        return JSONResponse(
            {"code": error.code},
            status_code=error.status,
            headers={"Cache-Control": "no-store, private"},
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
            {
                # Locations may contain arbitrary submitted mapping keys.
                "location": (
                    issue["loc"][0]
                    if issue["loc"] and issue["loc"][0] in {"body", "query", "path", "header"}
                    else "request"
                ),
                "type": issue["type"],
            }
            for issue in error.errors()[:20]
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
    app.add_exception_handler(HumanInputConflictError, _human_input_conflict)
    app.add_exception_handler(LeaseExpiredError, _lease_expired)
    app.add_exception_handler(InterventionAuthorizationError, _intervention_forbidden)
    app.add_exception_handler(DiscoverySuiteError, _discovery_suite_conflict)
    for conflict_error in (
        LeaseConflictError,
        InterventionConflictError,
        InterventionTransitionError,
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
                "published": False,
                "evidence_manifest": result.evidence_manifest,
            }
            return JSONResponse(payload, status_code=200)
        return JSONResponse(result.model_dump(mode="json"), status_code=200)

    @app.post("/api/v1/discovery-suites")
    def create_discovery_suite(request: Request, body: DiscoveryInvocation) -> JSONResponse:
        invoker = services.discovery_suite_invoker
        if invoker is None or not invoker.ready():
            return _error_response(
                request,
                status_code=503,
                code="discovery_not_ready",
                message="The configured discovery service is unavailable.",
                retryable=True,
            )
        suite = invoker.create(**body.model_dump())
        return JSONResponse(suite.snapshot(), status_code=200)

    @app.get("/api/v1/discovery-suites/{suite_id}")
    def get_discovery_suite(request: Request, suite_id: str) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return JSONResponse(invoker.get(suite_id).snapshot(), status_code=200)

    @app.get("/api/v1/discovery-suites/{suite_id}/artifact")
    def get_discovery_suite_artifact(request: Request, suite_id: str) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        artifact = invoker.published_artifact(suite_id)
        return JSONResponse(artifact.model_dump(mode="json"), status_code=200)

    @app.post("/api/v1/discovery-suites/{suite_id}/scenarios")
    def add_discovery_scenario(
        request: Request, suite_id: str, body: DiscoverySuiteScenario
    ) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        suite = invoker.add_scenario(suite_id, **body.model_dump())
        return JSONResponse(suite.snapshot(), status_code=200)

    @app.get("/api/v1/discovery-suites/{suite_id}/scenarios/{code}/artifact")
    def get_scenario_artifact(request: Request, suite_id: str, code: str) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return JSONResponse(invoker.scenario_artifact(suite_id, code).model_dump(mode="json"))

    @app.post("/api/v1/discovery-suites/{suite_id}/finalize")
    def finalize_discovery_suite(request: Request, suite_id: str) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return JSONResponse(invoker.finalize(suite_id).snapshot(), status_code=200)

    @app.post("/api/v1/discovery-suites/{suite_id}/validations")
    def validate_discovery_suite(
        request: Request, suite_id: str, body: DiscoverySuiteValidation
    ) -> JSONResponse:
        invoker = _discovery_suite_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return JSONResponse(
            invoker.validate(suite_id, tenant=body.tenant, inputs=body.inputs).snapshot(),
            status_code=200,
        )

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

    @app.get("/api/v1/interventions", response_model=InterventionListResponse)
    def list_interventions(
        request: Request,
        run_mode: InterventionRunMode | None = None,
    ) -> InterventionListResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        return InterventionListResponse(
            items=tuple(_transition_response(item) for item in invoker.list_active(run_mode))
        )

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
        response_model=ResumeInterventionResponse,
    )
    def resume_intervention(
        request: Request, intervention_id: str, body: LeaseTransitionRequest
    ) -> ResumeInterventionResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        resume = invoker.begin_resume(
            intervention_id, body.expected_lease_version, body.operator_id
        )
        transition = _transition_response(resume.transition)
        return ResumeInterventionResponse(
            **transition.model_dump(),
            result=resume.result,
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
                "x-replayforge-next-client-sequence": str(frame.next_client_sequence),
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
        "/api/v1/interventions/{intervention_id}/input",
        response_model=HumanInputResponse,
    )
    def intervention_input(
        request: Request, intervention_id: str, body: HumanInputRequest
    ) -> HumanInputResponse | JSONResponse:
        invoker = _intervention_invoker(request, services)
        if isinstance(invoker, JSONResponse):
            return invoker
        action: HumanInput
        if isinstance(body.input, TextInputPayload):
            action = HumanTextInput(body.input.text)
        elif isinstance(body.input, KeyInputPayload):
            action = HumanKeyInput(HumanKey(body.input.key))
        else:
            action = HumanPointerInput(body.input.x, body.input.y)
        receipt = invoker.send_input(
            intervention_id,
            body.expected_lease_version,
            body.operator_id,
            HumanInputCommand(
                client_sequence=body.client_sequence,
                source_frame_sequence=body.source_frame_sequence,
                viewport=Viewport(body.viewport_width, body.viewport_height),
                action=action,
            ),
        )
        return HumanInputResponse(
            accepted=True,
            client_sequence=receipt.client_sequence,
            source_frame_sequence=receipt.source_frame_sequence,
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


async def _intervention_forbidden(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=403,
        code="intervention_forbidden",
        message="The operator does not own this intervention.",
    )


async def _lease_expired(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=409,
        code="control_lease_expired",
        message="The operator lease expired. Reclaim the intervention to continue.",
    )


async def _human_input_conflict(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=409,
        code="human_input_conflict",
        message="The input lease, sequence, or source frame is stale.",
    )


async def _discovery_suite_conflict(request: Request, error: Exception) -> JSONResponse:
    del error
    return _error_response(
        request,
        status_code=409,
        code="discovery_suite_conflict",
        message="The discovery suite cannot perform that transition.",
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


def _discovery_suite_invoker(request: Request, services: ApiServices) -> Any:
    if services.discovery_suite_invoker is not None:
        return services.discovery_suite_invoker
    return _error_response(
        request,
        status_code=503,
        code="discovery_suite_unavailable",
        message="The discovery suite service is unavailable.",
        retryable=True,
    )


def _transition_response(
    transition: InterventionTransition,
) -> InterventionTransitionResponse:
    context = transition.intervention.context
    return InterventionTransitionResponse(
        intervention_id=str(transition.intervention.id),
        run_id=str(transition.intervention.run_id),
        session_id=str(transition.intervention.session_id),
        status=transition.intervention.status.value,
        control_owner=transition.lease.owner.value,
        lease_version=transition.lease.version,
        lease_expires_at=transition.lease.expires_at.isoformat().replace("+00:00", "Z"),
        run_mode=context.run_mode.value,
        application_family=context.application_family,
        tenant=context.tenant,
        task_summary=context.task_summary,
        capability_id=context.capability_id,
        capability_version=context.capability_version,
        capability_name=context.capability_name,
        step_id=context.step_id,
        trigger_code=transition.intervention.trigger_code,
        explanation=transition.intervention.explanation,
        surface_route=context.surface_route,
        created_at=transition.intervention.created_at.isoformat().replace("+00:00", "Z"),
    )
