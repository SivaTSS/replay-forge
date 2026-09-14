"""Versioned HTTP request and response models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from replayforge.discovery.constraints import (
    DEFAULT_DISCOVERY_STEPS,
    DEFAULT_DISCOVERY_TIMEOUT,
    MAX_DISCOVERY_STEPS,
    MAX_DISCOVERY_TIMEOUT,
    MIN_DISCOVERY_STEPS,
    MIN_DISCOVERY_TIMEOUT,
)
from replayforge.runs.results import RunResult


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplayInvocation(ApiModel):
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    version: str | None = Field(
        default=None, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
    )
    inputs: dict[str, Any]


class DiscoveryInvocation(ApiModel):
    goal: str = Field(min_length=10, max_length=1_000)
    application_family: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    entry_point: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    inputs: dict[str, Any]
    existing_capability_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
    )
    max_steps: int = Field(
        default=DEFAULT_DISCOVERY_STEPS,
        ge=MIN_DISCOVERY_STEPS,
        le=MAX_DISCOVERY_STEPS,
    )
    timeout_seconds: int = Field(
        default=int(DEFAULT_DISCOVERY_TIMEOUT.total_seconds()),
        ge=int(MIN_DISCOVERY_TIMEOUT.total_seconds()),
        le=int(MAX_DISCOVERY_TIMEOUT.total_seconds()),
    )


class ReplayLaunch(ReplayInvocation):
    mode: Literal["replay"]
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class DiscoveryLaunch(DiscoveryInvocation):
    mode: Literal["discovery"]
    validation_tenants: tuple[str, ...] = Field(default=(), max_length=10)


class LaunchRequest(ApiModel):
    execution: ReplayLaunch | DiscoveryLaunch = Field(discriminator="mode")


class ViewerStartResponse(ApiModel):
    execution_id: str = Field(pattern=r"^exe_[0-9a-f]{32}$")
    viewer_token: str = Field(min_length=32, max_length=100, repr=False)


class ViewerFrameMetadata(ApiModel):
    sequence: int = Field(ge=1)
    run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    phase: str = Field(min_length=1, max_length=100)
    captured_at: AwareDatetime
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    event_sequence: int = Field(ge=0)


class ViewerEvent(ApiModel):
    sequence: int = Field(ge=1)
    run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    phase: str = Field(min_length=1, max_length=100)
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    step_id: str | None
    occurred_at: AwareDatetime
    details: dict[str, JsonValue]


class ViewerSnapshot(ApiModel):
    execution_id: str = Field(pattern=r"^exe_[0-9a-f]{32}$")
    mode: Literal["replay", "discovery"]
    state: Literal["running", "paused", "success", "failure", "business_outcome", "terminated"]
    phase: str = Field(min_length=1, max_length=100)
    run_ids: tuple[str, ...]
    frames: tuple[ViewerFrameMetadata, ...]
    evicted_frames: int = Field(ge=0)
    events: tuple[ViewerEvent, ...]
    event_cursor: int = Field(ge=0)
    first_event_sequence: int | None = Field(default=None, ge=1)
    result: dict[str, JsonValue] | None
    retention_seconds: int = Field(ge=1)


class PublishedSuiteInvocation(ApiModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    inputs: dict[str, Any]


class DiscoverySuiteScenario(ApiModel):
    kind: Literal["business_outcome", "application_failure", "recovery"]
    goal: str = Field(min_length=10, max_length=1_000)
    inputs: dict[str, Any]
    code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str | None = Field(default=None, min_length=1, max_length=500)
    max_steps: int = Field(
        default=DEFAULT_DISCOVERY_STEPS,
        ge=MIN_DISCOVERY_STEPS,
        le=MAX_DISCOVERY_STEPS,
    )
    timeout_seconds: int = Field(
        default=int(DEFAULT_DISCOVERY_TIMEOUT.total_seconds()),
        ge=int(MIN_DISCOVERY_TIMEOUT.total_seconds()),
        le=int(MAX_DISCOVERY_TIMEOUT.total_seconds()),
    )


class DiscoverySuiteValidation(ApiModel):
    tenant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    inputs: dict[str, Any]


class ArtifactValidationRequest(ApiModel):
    yaml: str = Field(min_length=1, max_length=1_000_000)


class ArtifactValidationResponse(ApiModel):
    valid: bool
    capability_id: str
    version: str
    content_hash: str


class HealthResponse(ApiModel):
    status: str


class ErrorBody(ApiModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool
    details: list[dict[str, str]] = Field(default_factory=list)


class LeaseTransitionRequest(ApiModel):
    expected_lease_version: int = Field(ge=1)
    operator_id: str = Field(pattern=r"^[A-Za-z0-9_.@-]{2,100}$")


class TerminateInterventionRequest(ApiModel):
    expected_lease_version: int = Field(ge=1)
    operator_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.@-]{2,100}$")
    resolution: str = Field(min_length=3, max_length=500)


class InterventionTransitionResponse(ApiModel):
    intervention_id: str
    run_id: str
    session_id: str
    status: str
    control_owner: str
    lease_version: int
    lease_expires_at: str
    run_mode: Literal["discovery", "replay"]
    application_family: str
    tenant: str
    task_summary: str
    capability_id: str | None = None
    capability_version: str | None = None
    capability_name: str | None = None
    step_id: str | None = None
    trigger_code: str
    explanation: str
    surface_route: str
    created_at: str


class InterventionListResponse(ApiModel):
    items: tuple[InterventionTransitionResponse, ...]


class ResumeInterventionResponse(InterventionTransitionResponse):
    result: RunResult | None = None


class PointerInputPayload(ApiModel):
    kind: Literal["pointer"]
    x: int = Field(ge=0, lt=8_192)
    y: int = Field(ge=0, lt=8_192)


class TextInputPayload(ApiModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=1_000)


class KeyInputPayload(ApiModel):
    kind: Literal["key"]
    key: Literal[
        "Enter",
        "Escape",
        "Tab",
        "Shift+Tab",
        "Backspace",
        "Delete",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
    ]


HumanInputPayload = Annotated[
    PointerInputPayload | TextInputPayload | KeyInputPayload,
    Field(discriminator="kind"),
]


class HumanInputRequest(ApiModel):
    expected_lease_version: int = Field(ge=1)
    operator_id: str = Field(pattern=r"^[A-Za-z0-9_.@-]{2,100}$")
    client_sequence: int = Field(ge=1, le=2_147_483_647)
    source_frame_sequence: int = Field(ge=1, le=2_147_483_647)
    viewport_width: int = Field(ge=1, le=8_192)
    viewport_height: int = Field(ge=1, le=8_192)
    input: HumanInputPayload

    @model_validator(mode="after")
    def validate_pointer_bounds(self) -> HumanInputRequest:
        if isinstance(self.input, PointerInputPayload) and (
            self.input.x >= self.viewport_width or self.input.y >= self.viewport_height
        ):
            raise ValueError("pointer coordinates must fall inside the source viewport")
        return self


class HumanInputResponse(ApiModel):
    accepted: Literal[True]
    client_sequence: int
    source_frame_sequence: int
