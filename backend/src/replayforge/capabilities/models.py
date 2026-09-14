"""Version 1 capability artifact contract.

These models contain no execution behavior. They form the reviewed boundary between
model-driven discovery and deterministic replay.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from replayforge.policy.types import RISK_RANK, DataClassification, Risk

_FIELD_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STABLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SurfaceKind(StrEnum):
    WEB = "web"
    DESKTOP = "desktop"


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
    enum: tuple[str | int | bool, ...] = ()
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
            if self.pattern is not None or self.format is not None or self.enum:
                raise ValueError("object schemas cannot declare scalar constraints")
            if self.const is not None or self.min_length is not None or self.max_length is not None:
                raise ValueError("object schemas cannot declare scalar constraints")
            if self.example is not None:
                raise ValueError("object schema examples are not supported")
        elif self.properties or self.required:
            raise ValueError("only object schemas may declare properties or required fields")
        if len(set(self.required)) != len(self.required):
            raise ValueError("required properties must be unique")
        invalid_properties = [
            name for name in self.properties if not _FIELD_NAME_PATTERN.fullmatch(name)
        ]
        if invalid_properties:
            raise ValueError(f"property names are invalid: {sorted(invalid_properties)}")
        if self.type is not JsonValueType.STRING and (
            self.pattern is not None
            or self.format is not None
            or self.min_length is not None
            or self.max_length is not None
        ):
            raise ValueError("only string schemas may declare string constraints")
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
        if len(set(self.enum)) != len(self.enum):
            raise ValueError("enum values must be unique")
        if any(not _matches_json_type(self.type, item) for item in self.enum):
            raise ValueError("enum values must match the declared value type")
        if self.const is not None and not _matches_json_type(self.type, self.const):
            raise ValueError("const must match the declared value type")
        if self.example is not None and not _matches_json_type(self.type, self.example):
            raise ValueError("example must match the declared value type")
        if self.const is not None and self.enum and self.const not in self.enum:
            raise ValueError("const must be included in enum")
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
        if len(set(self.required)) != len(self.required):
            raise ValueError("required properties must be unique")
        invalid_properties = [
            name for name in self.properties if not _FIELD_NAME_PATTERN.fullmatch(name)
        ]
        if invalid_properties:
            raise ValueError(f"property names are invalid: {sorted(invalid_properties)}")
        return self


def _matches_json_type(value_type: JsonValueType, value: object) -> bool:
    if value_type is JsonValueType.STRING:
        return isinstance(value, str)
    if value_type is JsonValueType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is JsonValueType.BOOLEAN:
        return isinstance(value, bool)
    return False


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
    capture_group_label: str | None = Field(default=None, min_length=1, max_length=200)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
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
            if None in (
                self.x,
                self.y,
                self.width,
                self.height,
                self.viewport_width,
                self.viewport_height,
            ):
                raise ValueError("coordinate locator requires a region and viewport dimensions")
            assert self.x is not None and self.y is not None
            assert self.width is not None and self.height is not None
            assert self.viewport_width is not None and self.viewport_height is not None
            if (
                self.x + self.width > self.viewport_width
                or self.y + self.height > self.viewport_height
            ):
                raise ValueError("coordinate region must fit inside the recorded viewport")
            if self.portability is not Portability.LOW:
                raise ValueError("coordinate locator must declare low portability")
        common = {"strategy", "expected_count", "match", "portability"}
        strategy_fields = {
            LocatorStrategy.ROLE_NAME: {"role", "name"},
            LocatorStrategy.RELATIVE_TEXT: {"anchor", "relation", "element", "text"},
            LocatorStrategy.COORDINATES: {
                "capture_group_label",
                "x",
                "y",
                "width",
                "height",
                "viewport_width",
                "viewport_height",
            },
        }
        allowed = common | strategy_fields.get(self.strategy, {"value"})
        strategy_specific_fields = {
            "value",
            "role",
            "name",
            "anchor",
            "relation",
            "element",
            "text",
            "capture_group_label",
            "x",
            "y",
            "width",
            "height",
            "viewport_width",
            "viewport_height",
        }
        populated = {
            field_name
            for field_name in strategy_specific_fields
            if getattr(self, field_name) is not None
        }
        unexpected = populated - allowed
        if unexpected:
            raise ValueError(
                f"{self.strategy.value} locator contains unrelated fields: {sorted(unexpected)}"
            )
        return self


class NormalizedRegion(ArtifactModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("normalized region must fit inside the viewport")
        return self


class RelativeRegion(ArtifactModel):
    """Region expressed in multiples of the freshly observed anchor height."""

    x: float = Field(ge=-20, le=20)
    y: float = Field(ge=-20, le=20)
    width: float = Field(gt=0, le=40)
    height: float = Field(gt=0, le=40)


class OcrAnchor(ArtifactModel):
    """Rendered text used to scope a secondary visual target."""

    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT
    search_region: NormalizedRegion | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)


class OcrTextCandidate(ArtifactModel):
    strategy: Literal["ocr_text"]
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT
    search_region: NormalizedRegion | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)
    expected_count: Literal[1] = 1
    portability: Portability = Portability.HIGH


class OcrRelativeCandidate(ArtifactModel):
    strategy: Literal["ocr_relative"]
    anchor: str = Field(min_length=1, max_length=200)
    anchor_match: MatchMode = MatchMode.EXACT
    target_text: str | None = Field(default=None, min_length=1, max_length=200)
    relation: Literal["right_of", "below", "same_row"]
    relative_region: RelativeRegion | None = None
    search_region: NormalizedRegion | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)
    expected_count: Literal[1] = 1
    portability: Portability = Portability.HIGH

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if (self.target_text is None) == (self.relative_region is None):
            raise ValueError("ocr_relative requires exactly one target_text or relative_region")
        return self


class ImageAnchorCandidate(ArtifactModel):
    strategy: Literal["image_anchor"]
    asset_key: str = Field(pattern=r"^asset://sha256/[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    search_region: NormalizedRegion | None = None
    context_anchor: OcrAnchor | None = None
    relative_search_region: RelativeRegion | None = None
    minimum_score: float = Field(default=0.90, ge=0, le=1)
    uniqueness_margin: float = Field(default=0.08, ge=0, le=1)
    minimum_scale: float = Field(default=0.80, gt=0, le=4)
    maximum_scale: float = Field(default=1.25, gt=0, le=4)
    scale_step: float = Field(default=0.05, gt=0, le=0.5)
    expected_count: Literal[1] = 1
    portability: Portability = Portability.MEDIUM

    @model_validator(mode="after")
    def validate_scale_range(self) -> Self:
        if self.minimum_scale > self.maximum_scale:
            raise ValueError("minimum_scale cannot exceed maximum_scale")
        if (self.context_anchor is None) != (self.relative_search_region is None):
            raise ValueError("context_anchor and relative_search_region must be provided together")
        if self.context_anchor is not None and self.search_region is not None:
            raise ValueError(
                "contextual image anchors cannot also declare a top-level search_region"
            )
        return self


class RenderedTextCandidate(ArtifactModel):
    """Semantic text target resolved from the current rendered frame."""

    strategy: Literal["rendered_text"]
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT


class RenderedLabeledControlCandidate(ArtifactModel):
    """Visual control associated with a rendered label."""

    strategy: Literal["rendered_labeled_control"]
    label: str = Field(min_length=1, max_length=200)
    label_match: MatchMode = MatchMode.EXACT
    control_kind: Literal["text_input"]


class RenderedFieldValueCandidate(ArtifactModel):
    """Value associated with a rendered field label."""

    strategy: Literal["rendered_field_value"]
    label: str = Field(min_length=1, max_length=200)
    label_match: MatchMode = MatchMode.EXACT


class RenderedGroupImageCandidate(ArtifactModel):
    """Image identity scoped to the semantic group containing a label."""

    strategy: Literal["rendered_group_image"]
    group_label: str = Field(min_length=1, max_length=200)
    group_label_match: MatchMode = MatchMode.EXACT
    asset_key: str = Field(pattern=r"^asset://sha256/[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


VisualLocatorCandidate = Annotated[
    OcrTextCandidate
    | OcrRelativeCandidate
    | ImageAnchorCandidate
    | RenderedTextCandidate
    | RenderedLabeledControlCandidate
    | RenderedFieldValueCandidate
    | RenderedGroupImageCandidate,
    Field(discriminator="strategy"),
]


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
    registered_risk: Risk | None = None
    scope: LocatorScope = Field(default_factory=LocatorScope)
    visual_candidates: tuple[VisualLocatorCandidate, ...] = ()
    candidates: tuple[LocatorCandidate, ...] = ()
    state: TargetState = Field(default_factory=TargetState)
    tenant_overrides_allowed: bool = False

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if not self.visual_candidates and not self.candidates:
            raise ValueError("a locator bundle requires at least one candidate")
        return self


class RouteCondition(ArtifactModel):
    kind: Literal["route"]
    pattern: str = Field(min_length=1)


class TextCondition(ArtifactModel):
    kind: Literal["text"]
    value: str = Field(min_length=1)
    match: MatchMode = MatchMode.EXACT


class RenderedTextCondition(ArtifactModel):
    """Semantic text condition resolved from the current rendered frame."""

    kind: Literal["rendered_text"]
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT


class VisualTextCondition(ArtifactModel):
    kind: Literal["visual_text"]
    value: str = Field(min_length=1)
    match: MatchMode = MatchMode.EXACT
    search_region: NormalizedRegion | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)


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
    | RenderedTextCondition
    | VisualTextCondition
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


type KeyboardKey = Literal[
    "Enter",
    "Space",
    "Tab",
    "Escape",
    "Backspace",
    "Delete",
    "Insert",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
    "F6",
    "F7",
    "F8",
    "F9",
    "F10",
    "F11",
    "F12",
    "Control",
    "Shift",
    "Alt",
    "Meta",
    "A",
]


class PressKeysAction(ArtifactModel):
    kind: Literal["press_keys"]
    keys: tuple[KeyboardKey, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_chord(self) -> Self:
        modifiers = {"Control", "Shift", "Alt", "Meta"}
        if self.keys[-1] in modifiers or any(key not in modifiers for key in self.keys[:-1]):
            raise ValueError("a key chord requires modifiers followed by one supported key")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("a key chord cannot repeat a modifier")
        if self.keys[-1] == "A" and not ({"Control", "Meta"} & set(self.keys[:-1])):
            raise ValueError("text entry requires a type action; A is only a select-all shortcut")
        return self


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
    transform: Literal["text", "trim", "lowercase", "decimal", "date-time"] = "trim"


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
    require_effect_absent: Literal[True] = True

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if len(self.backoff_ms) > self.max_attempts - 1:
            raise ValueError("backoff entries cannot exceed the number of retries")
        if any(delay < 0 or delay > 30_000 for delay in self.backoff_ms):
            raise ValueError("backoff values must be between 0 and 30000 milliseconds")
        if len(set(self.retry_on)) != len(self.retry_on) or any(
            _STABLE_ID_PATTERN.fullmatch(code) is None for code in self.retry_on
        ):
            raise ValueError("retry error codes must be unique stable identifiers")
        if self.max_attempts > 1 and not self.retry_on:
            raise ValueError("multiple attempts require an explicit retry error code")
        if self.max_attempts == 1 and (self.retry_on or self.backoff_ms):
            raise ValueError("single-attempt steps cannot declare retry behavior")
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
    failure_refs: tuple[str, ...] = ()
    risk: Risk
    evidence: EvidenceRequirement = Field(default_factory=EvidenceRequirement)

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        requires_target = isinstance(
            self.action, ClickAction | TypeAction | SelectAction | ExtractAction
        )
        if requires_target and self.target is None:
            raise ValueError(f"{self.action.kind} action requires a target")
        if not requires_target and self.target is not None:
            raise ValueError(f"{self.action.kind} action cannot declare a target")
        for reference_name, references in (
            ("recovery", self.recovery_refs),
            ("outcome", self.outcome_refs),
            ("failure", self.failure_refs),
        ):
            if len(set(references)) != len(references):
                raise ValueError(f"step {reference_name} references must be unique")
        return self


class Recovery(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
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


class ApplicationFailure(ArtifactModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    description: str = Field(min_length=1)
    detect: Condition
    allowed_after_steps: tuple[str, ...] = Field(min_length=1)
    expected_state: str = Field(min_length=1)
    observed_state: str = Field(min_length=1)
    recoverable: bool = False


class Checkpoint(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    condition: Condition


class Landmark(ArtifactModel):
    kind: Literal["heading", "field", "text", "visual_text"]
    value: str


class SurfaceFingerprint(ArtifactModel):
    required_landmarks: tuple[Landmark, ...] = Field(min_length=1)
    forbidden_landmarks: tuple[Landmark, ...] = ()


class Compatibility(ArtifactModel):
    application_family: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    base_variant: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    supported_variants: tuple[str, ...] = Field(min_length=1)
    surface_contract: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    entry_point: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    rendered_surface: bool = False
    fingerprint: SurfaceFingerprint

    @model_validator(mode="after")
    def validate_variants(self) -> Self:
        if len(set(self.supported_variants)) != len(self.supported_variants):
            raise ValueError("supported variants must be unique")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", item) is None for item in self.supported_variants
        ):
            raise ValueError("supported variants contain an invalid identifier")
        return self


class CapabilityMetadata(ArtifactModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_000)
    application_family: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    surface: SurfaceKind
    risk: Risk
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_tags(self) -> Self:
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("capability tags must be unique")
        if any(
            not tag or len(tag) > 64 or _STABLE_ID_PATTERN.fullmatch(tag) is None
            for tag in self.tags
        ):
            raise ValueError("capability tags must be stable identifiers")
        return self


class CapabilityPolicy(ArtifactModel):
    allowed_action_types: frozenset[str]
    allowed_entry_points: frozenset[str]
    maximum_risk: Risk
    allowed_route_patterns: frozenset[str] = frozenset()
    forbidden_text_inputs: tuple[str, ...] = ()
    output_redaction: dict[str, Literal["remove", "last4", "tokenize"]] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_route_patterns(self) -> Self:
        known_actions = {
            "navigate",
            "click",
            "type",
            "press_keys",
            "select",
            "scroll",
            "wait_for",
            "extract",
            "assert",
            "switch_context",
            "checkpoint",
        }
        if not self.allowed_action_types or not self.allowed_action_types <= known_actions:
            raise ValueError("capability policy contains unknown action types")
        if not self.allowed_entry_points:
            raise ValueError("capability policy requires an entry point")
        for pattern in self.allowed_route_patterns:
            if (
                not pattern.startswith("/")
                or "?" in pattern
                or "#" in pattern
                or "//" in pattern
                or ".." in pattern
            ):
                raise ValueError("capability route patterns must be absolute paths")
        return self


class Provenance(ArtifactModel):
    discovery_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    prompt_policy_version: str = Field(min_length=1, max_length=100)
    surface_adapter_version: str = Field(min_length=1, max_length=100)
    compiler_version: str = Field(min_length=1, max_length=100)
    created_at: datetime
    target_fingerprint: str = Field(min_length=1, max_length=200)
    evidence_manifest_key: str = Field(pattern=r"^evidence://[^\s]+$")
    artifact_content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("provenance timestamp must include an offset")
        return self


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


def _walk_conditions(condition: Condition) -> tuple[Condition, ...]:
    if isinstance(condition, AllCondition | AnyCondition):
        return (
            condition,
            *(item for nested in condition.conditions for item in _walk_conditions(nested)),
        )
    if isinstance(condition, NotCondition):
        return (condition, *_walk_conditions(condition.condition))
    return (condition,)


def _contract_has_path(contract: ObjectContract, path: str) -> bool:
    parts = path.split(".")
    properties = contract.properties
    for index, part in enumerate(parts):
        schema = properties.get(part)
        if schema is None:
            return False
        if index == len(parts) - 1:
            return True
        if schema.type is not JsonValueType.OBJECT:
            return False
        properties = schema.properties
    return False


def _validate_condition_references(
    condition: Condition, inputs: ObjectContract, outputs: ObjectContract
) -> None:
    for item in _walk_conditions(condition):
        if isinstance(item, OutputValidCondition) and item.output not in outputs.properties:
            raise ValueError(f"condition references unknown output {item.output}")
        if isinstance(item, IdentityMatchesCondition):
            if item.extracted_output not in outputs.properties:
                raise ValueError(
                    f"identity condition references unknown output {item.extracted_output}"
                )
            if not _contract_has_path(inputs, item.input_path):
                raise ValueError(f"identity condition references unknown input {item.input_path}")


_GEOMETRY_FREE_CANDIDATE_TYPES = (
    RenderedTextCandidate,
    RenderedLabeledControlCandidate,
    RenderedFieldValueCandidate,
    RenderedGroupImageCandidate,
)


def _is_geometry_free_candidate(candidate: VisualLocatorCandidate) -> bool:
    return isinstance(candidate, _GEOMETRY_FREE_CANDIDATE_TYPES) or (
        isinstance(candidate, OcrRelativeCandidate)
        and candidate.target_text is not None
        and candidate.relative_region is None
        and candidate.search_region is None
    )


def _validate_geometry_free_target(target: LocatorBundle) -> None:
    if target.candidates:
        raise ValueError("schema 1.3 visual targets cannot contain DOM or coordinate locators")
    if not target.visual_candidates:
        raise ValueError("schema 1.3 visual targets require rendered candidates")
    if not all(_is_geometry_free_candidate(candidate) for candidate in target.visual_candidates):
        raise ValueError("schema 1.3 visual targets require geometry-free rendered candidates")


def _validate_geometry_free_condition(condition: Condition) -> None:
    if isinstance(condition, RenderedTextCondition):
        return
    if isinstance(condition, TextCondition | VisualTextCondition):
        raise ValueError("rendered-surface conditions must use geometry-free rendered text")
    if isinstance(condition, ElementCondition):
        raise ValueError("rendered-surface conditions cannot contain DOM element locators")
    if isinstance(condition, AllCondition | AnyCondition):
        for nested in condition.conditions:
            _validate_geometry_free_condition(nested)
        return
    if isinstance(condition, NotCondition):
        _validate_geometry_free_condition(condition.condition)


def _validate_geometry_free_visual_contract(artifact: CapabilityArtifact) -> None:
    for step in (
        *artifact.steps,
        *(step for recovery in artifact.recoveries for step in recovery.steps),
    ):
        if step.target is not None:
            _validate_geometry_free_target(step.target)
        for condition in (*step.preconditions, *step.postconditions):
            _validate_geometry_free_condition(condition)
    for condition in artifact.preconditions:
        _validate_geometry_free_condition(condition)
    for recovery in artifact.recoveries:
        _validate_geometry_free_condition(recovery.trigger)
    for outcome in artifact.outcomes:
        _validate_geometry_free_condition(outcome.detect)
    for failure in artifact.failures:
        _validate_geometry_free_condition(failure.detect)
    _validate_geometry_free_condition(artifact.checkpoint.condition)


def _validate_no_persisted_coordinates(artifact: CapabilityArtifact) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("strategy") == LocatorStrategy.COORDINATES.value:
                raise ValueError("schema 1.4 artifacts cannot persist coordinate locators")
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(artifact.model_dump(mode="json"))


class CapabilityArtifact(ArtifactModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4"]
    capability: CapabilityMetadata
    compatibility: Compatibility
    inputs: ObjectContract
    outputs: ObjectContract
    preconditions: tuple[Condition, ...] = ()
    steps: tuple[Step, ...] = Field(min_length=1)
    recoveries: tuple[Recovery, ...] = ()
    outcomes: tuple[BusinessOutcome, ...] = ()
    failures: tuple[ApplicationFailure, ...] = ()
    checkpoint: Checkpoint
    policy: CapabilityPolicy
    provenance: Provenance

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.schema_version == "1.3":
            _validate_geometry_free_visual_contract(self)
        if self.schema_version == "1.4":
            _validate_no_persisted_coordinates(self)
            if not self.policy.allowed_route_patterns:
                raise ValueError("schema 1.4 artifacts require non-empty route patterns")
            if self.compatibility.rendered_surface:
                _validate_geometry_free_visual_contract(self)
            if re.fullmatch(r"[0-9a-f]{64}", self.provenance.target_fingerprint) is None:
                raise ValueError("schema 1.4 artifacts require a SHA-256 target fingerprint")
            expected_evidence_prefix = f"evidence://{self.provenance.discovery_run_id}/"
            if not self.provenance.evidence_manifest_key.startswith(expected_evidence_prefix):
                raise ValueError("schema 1.4 provenance evidence must belong to its discovery run")
        if self.capability.application_family != self.compatibility.application_family:
            raise ValueError("capability and compatibility application families must match")
        if self.capability.risk is not self.policy.maximum_risk:
            raise ValueError("capability risk and policy maximum risk must match")
        if self.compatibility.entry_point not in self.policy.allowed_entry_points:
            raise ValueError("compatibility entry point is not allowed by capability policy")
        if self.policy.output_redaction.keys() - self.outputs.properties.keys():
            raise ValueError("output redaction references an unknown output")
        if any(not term.strip() for term in self.policy.forbidden_text_inputs):
            raise ValueError("forbidden text inputs must be non-empty")

        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique")
        main_step_ids = set(step_ids)
        recovery_ids = {recovery.id for recovery in self.recoveries}
        if len(recovery_ids) != len(self.recoveries):
            raise ValueError("recovery IDs must be unique")
        outcome_codes = {outcome.code for outcome in self.outcomes}
        failure_codes = {failure.code for failure in self.failures}
        if len(outcome_codes) != len(self.outcomes):
            raise ValueError("business outcome codes must be unique")
        if len(failure_codes) != len(self.failures):
            raise ValueError("application failure codes must be unique")
        if outcome_codes & failure_codes:
            raise ValueError("business outcome and application failure codes must be distinct")
        outcome_steps = {
            outcome.code: frozenset(outcome.allowed_after_steps) for outcome in self.outcomes
        }
        failure_steps = {
            failure.code: frozenset(failure.allowed_after_steps) for failure in self.failures
        }
        for outcome in self.outcomes:
            if len(set(outcome.allowed_after_steps)) != len(outcome.allowed_after_steps):
                raise ValueError(f"outcome {outcome.code} allowed steps must be unique")
            for source in outcome.result.details.values():
                if isinstance(source, InputValue) and not _contract_has_path(
                    self.inputs, source.path
                ):
                    raise ValueError(f"outcome {outcome.code} references an unknown input")
        for failure in self.failures:
            if len(set(failure.allowed_after_steps)) != len(failure.allowed_after_steps):
                raise ValueError(f"application failure {failure.code} allowed steps must be unique")

        bound_outputs: set[str] = set()
        recovery_steps = tuple(
            recovery_step for recovery in self.recoveries for recovery_step in recovery.steps
        )
        all_step_ids = [*step_ids, *(step.id for step in recovery_steps)]
        all_step_id_set = set(all_step_ids)
        if len(all_step_ids) != len(set(all_step_ids)):
            raise ValueError("main and recovery step IDs must be unique")
        for outcome in self.outcomes:
            if not set(outcome.allowed_after_steps) <= all_step_id_set:
                raise ValueError(f"outcome {outcome.code} references an unknown step")
        for failure in self.failures:
            if not set(failure.allowed_after_steps) <= all_step_id_set:
                raise ValueError(f"application failure {failure.code} references an unknown step")
        for step in (*self.steps, *recovery_steps):
            is_recovery_step = step.id not in main_step_ids
            if step.action.kind not in self.policy.allowed_action_types:
                raise ValueError(f"step {step.id} uses a disallowed action type")
            if RISK_RANK[step.risk] > RISK_RANK[self.policy.maximum_risk]:
                raise ValueError(f"step {step.id} exceeds the capability risk ceiling")
            if is_recovery_step and RISK_RANK[step.risk] >= RISK_RANK[Risk.SENSITIVE]:
                raise ValueError(f"recovery step {step.id} cannot require human approval")
            if (
                isinstance(step.action, NavigateAction)
                and step.action.entry_point not in self.policy.allowed_entry_points
            ):
                raise ValueError(f"step {step.id} uses a disallowed entry point")
            input_source = (
                step.action.value
                if isinstance(step.action, TypeAction)
                else step.action.option
                if isinstance(step.action, SelectAction)
                else None
            )
            if isinstance(input_source, InputValue) and (
                not _contract_has_path(self.inputs, input_source.path)
            ):
                raise ValueError(f"step {step.id} references an unknown input")
            if isinstance(step.action, ExtractAction):
                if step.action.output not in self.outputs.properties:
                    raise ValueError(f"step {step.id} binds an unknown output")
                if not is_recovery_step:
                    bound_outputs.add(step.action.output)
            if not set(step.recovery_refs) <= recovery_ids:
                raise ValueError(f"step {step.id} references an unknown recovery")
            if not set(step.outcome_refs) <= outcome_codes:
                raise ValueError(f"step {step.id} references an unknown business outcome")
            if not set(step.failure_refs) <= failure_codes:
                raise ValueError(f"step {step.id} references an unknown application failure")
            if isinstance(step.action, CheckpointAction) and (
                step.action.checkpoint_id != self.checkpoint.id
            ):
                raise ValueError(f"step {step.id} references an unknown checkpoint")
            for outcome_code in step.outcome_refs:
                if step.id not in outcome_steps[outcome_code]:
                    raise ValueError(f"outcome {outcome_code} is not allowed after step {step.id}")
            for failure_code in step.failure_refs:
                if step.id not in failure_steps[failure_code]:
                    raise ValueError(
                        f"application failure {failure_code} is not allowed after step {step.id}"
                    )
            for condition in (*step.preconditions, *step.postconditions):
                _validate_condition_references(condition, self.inputs, self.outputs)
            if isinstance(step.action, AssertAction | WaitForAction):
                _validate_condition_references(step.action.condition, self.inputs, self.outputs)

        for condition in self.preconditions:
            _validate_condition_references(condition, self.inputs, self.outputs)
        for recovery in self.recoveries:
            _validate_condition_references(recovery.trigger, self.inputs, self.outputs)
        for outcome in self.outcomes:
            _validate_condition_references(outcome.detect, self.inputs, self.outputs)
        for failure in self.failures:
            _validate_condition_references(failure.detect, self.inputs, self.outputs)
        _validate_condition_references(self.checkpoint.condition, self.inputs, self.outputs)

        missing_bindings = set(self.outputs.required) - bound_outputs
        if missing_bindings:
            raise ValueError(f"required outputs are not bound: {sorted(missing_bindings)}")
        checkpoint_outputs = _condition_outputs(self.checkpoint.condition)
        missing_checks = set(self.outputs.required) - checkpoint_outputs
        if missing_checks:
            raise ValueError(
                f"checkpoint does not validate required outputs: {sorted(missing_checks)}"
            )

        known_steps = main_step_ids
        for recovery in self.recoveries:
            if any(step.recovery_refs for step in recovery.steps):
                raise ValueError(f"recovery {recovery.id} cannot invoke a nested recovery")
            if recovery.resume_at not in known_steps:
                raise ValueError(f"recovery {recovery.id} resumes at an unknown step")
        return self
