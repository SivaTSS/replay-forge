"""Version 1 capability artifact contract.

These models contain no execution behavior. They form the reviewed boundary between
model-driven discovery and deterministic replay.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SurfaceKind(StrEnum):
    WEB = "web"
    DESKTOP = "desktop"


class Risk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    SENSITIVE = "sensitive"
    IRREVERSIBLE = "irreversible"


RISK_RANK = {risk: rank for rank, risk in enumerate(Risk)}


class DataClassification(StrEnum):
    PUBLIC = "public"
    OPERATIONAL = "operational"
    CUSTOMER_IDENTIFIER = "customer_identifier"
    PERSONAL = "personal"
    FINANCIAL = "financial"
    CREDENTIAL = "credential"
    SECRET = "secret"


class PersistenceMode(StrEnum):
    FULL = "full"
    REDACTED = "redacted"
    FORBIDDEN = "forbidden"


class JsonValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    OBJECT = "object"


class ValueSchema(ArtifactModel):
    type: JsonValueType
    description: str = Field(min_length=1)
    data_classification: DataClassification
    persistence: PersistenceMode = PersistenceMode.REDACTED
    required: tuple[str, ...] = ()
    properties: dict[str, ValueSchema] = Field(default_factory=dict)
    additional_properties: Literal[False] = False
    pattern: str | None = None
    format: Literal["date", "date-time", "decimal"] | None = None
    enum: tuple[str, ...] = ()
    const: str | int | bool | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    example: str | int | bool | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.type is JsonValueType.OBJECT:
            unknown = set(self.required) - self.properties.keys()
            if unknown:
                raise ValueError(f"required properties are not declared: {sorted(unknown)}")
        elif self.properties or self.required:
            raise ValueError("only object schemas may declare properties or required fields")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length cannot exceed max_length")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("pattern must be a valid regular expression") from exc
        return self


class ObjectContract(ArtifactModel):
    type: Literal[JsonValueType.OBJECT] = JsonValueType.OBJECT
    additional_properties: Literal[False] = False
    required: tuple[str, ...]
    properties: dict[str, ValueSchema]

    @model_validator(mode="after")
    def validate_required_properties(self) -> Self:
        unknown = set(self.required) - self.properties.keys()
        if unknown:
            raise ValueError(f"required properties are not declared: {sorted(unknown)}")
        return self


class InputValue(ArtifactModel):
    source: Literal["input"]
    path: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class LiteralValue(ArtifactModel):
    source: Literal["literal"]
    value: str | int | bool


ValueSource = Annotated[InputValue | LiteralValue, Field(discriminator="source")]


class MatchMode(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"
    REGEX = "regex"


class LocatorStrategy(StrEnum):
    ROLE_NAME = "role_name"
    LABEL = "label"
    TEXT = "text"
    RELATIVE_TEXT = "relative_text"
    PLACEHOLDER = "placeholder"
    TITLE = "title"
    CSS = "css"
    ACCESSIBILITY_PATH = "accessibility_path"
    IMAGE_ANCHOR = "image_anchor"
    COORDINATES = "coordinates"


class Portability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LocatorCandidate(ArtifactModel):
    strategy: LocatorStrategy
    expected_count: int = Field(default=1, ge=1, le=20)
    value: str | None = None
    role: str | None = None
    name: str | None = None
    match: MatchMode = MatchMode.EXACT
    anchor: str | None = None
    relation: str | None = None
    element: str | None = None
    text: str | None = None
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    viewport_width: int | None = Field(default=None, gt=0)
    viewport_height: int | None = Field(default=None, gt=0)
    portability: Portability = Portability.HIGH

    @model_validator(mode="after")
    def validate_strategy_fields(self) -> Self:
        if self.strategy is LocatorStrategy.ROLE_NAME and not (self.role and self.name):
            raise ValueError("role_name locator requires role and name")
        if self.strategy is LocatorStrategy.RELATIVE_TEXT and not (
            self.anchor and self.relation and self.element
        ):
            raise ValueError("relative_text locator requires anchor, relation, and element")
        value_strategies = {
            LocatorStrategy.LABEL,
            LocatorStrategy.TEXT,
            LocatorStrategy.PLACEHOLDER,
            LocatorStrategy.TITLE,
            LocatorStrategy.CSS,
            LocatorStrategy.ACCESSIBILITY_PATH,
            LocatorStrategy.IMAGE_ANCHOR,
        }
        if self.strategy in value_strategies and not self.value:
            raise ValueError(f"{self.strategy.value} locator requires value")
        if self.strategy is LocatorStrategy.COORDINATES:
            if None in (self.x, self.y, self.viewport_width, self.viewport_height):
                raise ValueError("coordinate locator requires point and viewport dimensions")
            if self.portability is not Portability.LOW:
                raise ValueError("coordinate locator must declare low portability")
        return self


class FrameLocator(ArtifactModel):
    locator: LocatorCandidate


class LocatorScope(ArtifactModel):
    window: str = "primary"
    frame_path: tuple[FrameLocator, ...] = ()


class TargetState(ArtifactModel):
    visible: bool = True
    enabled: bool | None = None


class LocatorBundle(ArtifactModel):
    description: str = Field(min_length=1)
    scope: LocatorScope = Field(default_factory=LocatorScope)
    candidates: tuple[LocatorCandidate, ...] = Field(min_length=1)
    state: TargetState = Field(default_factory=TargetState)
    tenant_overrides_allowed: bool = False


class RouteCondition(ArtifactModel):
    kind: Literal["route"]
    pattern: str = Field(min_length=1)


class TextCondition(ArtifactModel):
    kind: Literal["text"]
    value: str = Field(min_length=1)
    match: MatchMode = MatchMode.EXACT


class ElementCondition(ArtifactModel):
    kind: Literal["element"]
    target: LocatorBundle
    state: Literal["exists", "absent", "visible", "hidden", "enabled", "disabled"]


class OutputValidCondition(ArtifactModel):
    kind: Literal["output_valid"]
    output: str


class IdentityMatchesCondition(ArtifactModel):
    kind: Literal["identity_matches"]
    extracted_output: str
    input_path: str


class AllCondition(ArtifactModel):
    kind: Literal["all"]
    conditions: tuple[Condition, ...] = Field(min_length=1)


class AnyCondition(ArtifactModel):
    kind: Literal["any"]
    conditions: tuple[Condition, ...] = Field(min_length=1)


class NotCondition(ArtifactModel):
    kind: Literal["not"]
    condition: Condition


Condition = Annotated[
    RouteCondition
    | TextCondition
    | ElementCondition
    | OutputValidCondition
    | IdentityMatchesCondition
    | AllCondition
    | AnyCondition
    | NotCondition,
    Field(discriminator="kind"),
]


class NavigateAction(ArtifactModel):
    kind: Literal["navigate"]
    entry_point: str


class ClickAction(ArtifactModel):
    kind: Literal["click"]


class TypeAction(ArtifactModel):
    kind: Literal["type"]
    value: ValueSource
    clear: bool = True


class PressKeysAction(ArtifactModel):
    kind: Literal["press_keys"]
    keys: tuple[str, ...] = Field(min_length=1, max_length=4)


class SelectAction(ArtifactModel):
    kind: Literal["select"]
    option: ValueSource


class ScrollAction(ArtifactModel):
    kind: Literal["scroll"]
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(gt=0, le=2000)


class WaitForAction(ArtifactModel):
    kind: Literal["wait_for"]
    condition: Condition


class ExtractAction(ArtifactModel):
    kind: Literal["extract"]
    output: str
    transform: Literal["text", "trim", "decimal", "date-time"] = "trim"


class AssertAction(ArtifactModel):
    kind: Literal["assert"]
    condition: Condition


class SwitchContextAction(ArtifactModel):
    kind: Literal["switch_context"]
    context: str


class CheckpointAction(ArtifactModel):
    kind: Literal["checkpoint"]
    checkpoint_id: str


Action = Annotated[
    NavigateAction
    | ClickAction
    | TypeAction
    | PressKeysAction
    | SelectAction
    | ScrollAction
    | WaitForAction
    | ExtractAction
    | AssertAction
    | SwitchContextAction
    | CheckpointAction,
    Field(discriminator="kind"),
]


class RetryPolicy(ArtifactModel):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_ms: tuple[int, ...] = ()
    retry_on: tuple[str, ...] = ()
    require_effect_absent: bool = True

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if len(self.backoff_ms) > self.max_attempts - 1:
            raise ValueError("backoff entries cannot exceed the number of retries")
        if any(delay < 0 or delay > 30_000 for delay in self.backoff_ms):
            raise ValueError("backoff values must be between 0 and 30000 milliseconds")
        return self


class EvidenceRequirement(ArtifactModel):
    before: bool = False
    after: bool = True
    on_failure: bool = True


class Step(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    name: str = Field(min_length=1)
    action: Action
    target: LocatorBundle | None = None
    preconditions: tuple[Condition, ...] = ()
    postconditions: tuple[Condition, ...] = ()
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    recovery_refs: tuple[str, ...] = ()
    outcome_refs: tuple[str, ...] = ()
    risk: Risk
    evidence: EvidenceRequirement = Field(default_factory=EvidenceRequirement)

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        requires_target = isinstance(
            self.action, ClickAction | TypeAction | SelectAction | ExtractAction
        )
        if requires_target and self.target is None:
            raise ValueError(f"{self.action.kind} action requires a target")
        return self


class Recovery(ArtifactModel):
    id: str
    trigger: Condition
    max_uses: int = Field(ge=1, le=3)
    steps: tuple[Step, ...] = Field(min_length=1)
    resume_at: str


class OutcomeResult(ArtifactModel):
    status: Literal["business_outcome"] = "business_outcome"
    details: dict[str, ValueSource] = Field(default_factory=dict)


class BusinessOutcome(ArtifactModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    description: str
    detect: Condition
    allowed_after_steps: tuple[str, ...] = Field(min_length=1)
    result: OutcomeResult = Field(default_factory=OutcomeResult)


class Checkpoint(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    condition: Condition


class Landmark(ArtifactModel):
    kind: Literal["heading", "field", "text"]
    value: str


class SurfaceFingerprint(ArtifactModel):
    required_landmarks: tuple[Landmark, ...] = Field(min_length=1)
    forbidden_landmarks: tuple[Landmark, ...] = ()


class Compatibility(ArtifactModel):
    application_family: str
    base_variant: str
    supported_variants: tuple[str, ...] = Field(min_length=1)
    surface_contract: str
    entry_point: str
    fingerprint: SurfaceFingerprint


class CapabilityMetadata(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    name: str
    description: str
    application_family: str
    surface: SurfaceKind
    risk: Risk
    tags: tuple[str, ...] = ()


class CapabilityPolicy(ArtifactModel):
    allowed_action_types: frozenset[str]
    allowed_entry_points: frozenset[str]
    maximum_risk: Risk
    forbidden_text_inputs: tuple[str, ...] = ()
    output_redaction: dict[str, Literal["remove", "last4", "tokenize"]] = Field(
        default_factory=dict
    )


class Provenance(ArtifactModel):
    discovery_run_id: str
    provider: str
    model: str
    prompt_policy_version: str
    surface_adapter_version: str
    compiler_version: str
    created_at: datetime
    target_fingerprint: str
    evidence_manifest_key: str
    artifact_content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


def _condition_outputs(condition: Condition) -> set[str]:
    if isinstance(condition, OutputValidCondition):
        return {condition.output}
    if isinstance(condition, IdentityMatchesCondition):
        return {condition.extracted_output}
    if isinstance(condition, AllCondition | AnyCondition):
        return set().union(*(_condition_outputs(item) for item in condition.conditions))
    if isinstance(condition, NotCondition):
        return _condition_outputs(condition.condition)
    return set()


class CapabilityArtifact(ArtifactModel):
    schema_version: Literal["1.0"]
    capability: CapabilityMetadata
    compatibility: Compatibility
    inputs: ObjectContract
    outputs: ObjectContract
    preconditions: tuple[Condition, ...] = ()
    steps: tuple[Step, ...] = Field(min_length=1)
    recoveries: tuple[Recovery, ...] = ()
    outcomes: tuple[BusinessOutcome, ...] = ()
    checkpoint: Checkpoint
    policy: CapabilityPolicy
    provenance: Provenance

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.capability.application_family != self.compatibility.application_family:
            raise ValueError("capability and compatibility application families must match")
        if self.capability.risk is not self.policy.maximum_risk:
            raise ValueError("capability risk and policy maximum risk must match")
        if self.compatibility.entry_point not in self.policy.allowed_entry_points:
            raise ValueError("compatibility entry point is not allowed by capability policy")

        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique")
        recovery_ids = {recovery.id for recovery in self.recoveries}
        outcome_codes = {outcome.code for outcome in self.outcomes}

        bound_outputs: set[str] = set()
        for step in self.steps:
            if step.action.kind not in self.policy.allowed_action_types:
                raise ValueError(f"step {step.id} uses a disallowed action type")
            if RISK_RANK[step.risk] > RISK_RANK[self.policy.maximum_risk]:
                raise ValueError(f"step {step.id} exceeds the capability risk ceiling")
            if (
                isinstance(step.action, NavigateAction)
                and step.action.entry_point not in self.policy.allowed_entry_points
            ):
                raise ValueError(f"step {step.id} uses a disallowed entry point")
            if (
                isinstance(step.action, TypeAction)
                and isinstance(step.action.value, InputValue)
                and step.action.value.path.split(".", 1)[0] not in self.inputs.properties
            ):
                raise ValueError(f"step {step.id} references an unknown input")
            if isinstance(step.action, ExtractAction):
                if step.action.output not in self.outputs.properties:
                    raise ValueError(f"step {step.id} binds an unknown output")
                bound_outputs.add(step.action.output)
            if not set(step.recovery_refs) <= recovery_ids:
                raise ValueError(f"step {step.id} references an unknown recovery")
            if not set(step.outcome_refs) <= outcome_codes:
                raise ValueError(f"step {step.id} references an unknown business outcome")

        missing_bindings = set(self.outputs.required) - bound_outputs
        if missing_bindings:
            raise ValueError(f"required outputs are not bound: {sorted(missing_bindings)}")
        checkpoint_outputs = _condition_outputs(self.checkpoint.condition)
        missing_checks = set(self.outputs.required) - checkpoint_outputs
        if missing_checks:
            raise ValueError(
                f"checkpoint does not validate required outputs: {sorted(missing_checks)}"
            )

        known_steps = set(step_ids)
        for recovery in self.recoveries:
            if recovery.resume_at not in known_steps:
                raise ValueError(f"recovery {recovery.id} resumes at an unknown step")
        for outcome in self.outcomes:
            if not set(outcome.allowed_after_steps) <= known_steps:
                raise ValueError(f"outcome {outcome.code} references an unknown step")
        return self
