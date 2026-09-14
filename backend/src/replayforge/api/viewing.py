"""No-cache, capability-authorized read-only execution viewer endpoints."""

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import JSONResponse

from replayforge.api.contracts import LaunchRequest
from replayforge.api.services import ExecutionController
from replayforge.capabilities.values import ContractValidationError
from replayforge.runs.viewing import ViewingError

HEADERS = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"}


def viewing_router(controller: ExecutionController) -> APIRouter:
    router = APIRouter(prefix="/api/v1/executions")

    @router.get("/catalog")
    def catalog() -> JSONResponse:
        return JSONResponse(controller.catalog(), headers=HEADERS)

    @router.post("")
    def start(body: LaunchRequest) -> JSONResponse:
        try:
            started = controller.start(body)
        except (ContractValidationError, ValueError) as error:
            del error
            raise ViewingError("execution_input_invalid", 422) from None
        return JSONResponse(started, status_code=202, headers=HEADERS)

    @router.get("/{execution_id}")
    def snapshot(
        execution_id: str,
        x_viewer_token: str = Header(default="", max_length=100),
        after: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        return JSONResponse(
            controller.viewer.snapshot(execution_id, x_viewer_token, after), headers=HEADERS
        )

    @router.get("/{execution_id}/frames/{sequence}")
    def frame(
        execution_id: str,
        sequence: int,
        x_viewer_token: str = Header(default="", max_length=100),
    ) -> Response:
        return Response(
            controller.viewer.frame(execution_id, x_viewer_token, sequence),
            media_type="image/png",
            headers=HEADERS,
        )

    return router
