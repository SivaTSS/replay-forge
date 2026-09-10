"""Thin FastAPI adapter with sanitized, correlation-aware errors."""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from replayforge.api.contracts import (
    ArtifactValidationRequest,
    ArtifactValidationResponse,
    ErrorBody,
    HealthResponse,
    ReplayInvocation,
)
from replayforge.api.services import ApiServices
from replayforge.capabilities.serialization import (
    ArtifactParseError,
    artifact_content_hash,
    artifact_json_schema,
    load_artifact_yaml,
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
