"""Fail-closed compiler for the evaluated savings-balance capability."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    BusinessOutcome,
    CapabilityArtifact,
    CapabilityMetadata,
    CapabilityPolicy,
    Checkpoint,
    ClickAction,
    Compatibility,
    Condition,
    ExtractAction,
    IdentityMatchesCondition,
    InputValue,
    JsonValueType,
    Landmark,
    LiteralValue,
    LocatorStrategy,
    MatchMode,
    NotCondition,
    ObjectContract,
    OutcomeResult,
    OutputValidCondition,
    PersistenceMode,
    Provenance,
    RenderedTextCondition,
    RetryPolicy,
    RouteCondition,
    SelectAction,
    Step,
    SurfaceFingerprint,
    SurfaceKind,
    TextCondition,
    TypeAction,
    ValueSchema,
    VisualTextCondition,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.discovery.models import CapabilityDraftSpec, RecordedDiscoveryStep
from replayforge.policy.types import RISK_RANK, DataClassification, Risk
from replayforge.shared.clock import Clock
from replayforge.surfaces.models import NormalizedObservation


class CompilationError(ValueError):
    """The successful trace is insufficient for deterministic compilation."""


_REQUIRED_OUTPUTS = (
    "member_id",
    "account_type",
    "currency",
    "available_balance",
    "as_of",
)


@dataclass(frozen=True, slots=True)
class LegacySavingsBalanceCompiler:
    clock: Clock
    compiler_version: str = "1.0.0"
    surface_adapter_version: str = "web.v1"

    @property
    def required_output_names(self) -> tuple[str, ...]:
        return self.output_contract.required

    @property
    def output_contract(self) -> ObjectContract:
        return self._outputs()

    def compile(
        self,
        *,
        run_id: str,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        steps: tuple[RecordedDiscoveryStep, ...],
        final_observation: NormalizedObservation,
        provider_name: str,
        model_name: str,
        evidence_manifest: str,
    ) -> CapabilityArtifact:
        self._validate_trace(steps)
        visual_mode = any(step.target and step.target.visual_candidates for step in steps)
        semantic_visual_mode = any(
            step.target
            and any(
                candidate.strategy
                in {
                    "rendered_text",
                    "rendered_labeled_control",
                    "rendered_field_value",
                    "rendered_group_image",
                }
                for candidate in step.target.visual_candidates
            )
            for step in steps
        )
        compiled_steps = self._compile_steps(
            steps, visual_mode=visual_mode, semantic_visual_mode=semantic_visual_mode
        )
        artifact = CapabilityArtifact(
            schema_version=("1.3" if semantic_visual_mode else "1.1" if visual_mode else "1.0"),
            capability=CapabilityMetadata(
                id="member.lookup_savings_balance",
                version="3.2.0" if semantic_visual_mode else "3.0.0" if visual_mode else "1.0.0",
                name="Lookup savings balance",
                description=goal.strip(),
                application_family=application_family,
                surface=SurfaceKind.WEB,
                risk=Risk.READ_ONLY,
                tags=("member-service", "balance", "read-only"),
            ),
            compatibility=Compatibility(
                application_family=application_family,
                base_variant="standard",
                supported_variants=(tenant,),
                surface_contract="web.v1",
                entry_point=entry_point,
                fingerprint=SurfaceFingerprint(
                    required_landmarks=(
                        Landmark(
                            kind="visual_text" if visual_mode else "heading",
                            value="Member Search",
                        ),
                    ),
                    forbidden_landmarks=(Landmark(kind="text", value="System maintenance"),),
                ),
            ),
            inputs=self._inputs(),
            outputs=self._outputs(),
            preconditions=(RouteCondition(kind="route", pattern="/members/search"),),
            steps=compiled_steps,
            outcomes=(
                self._member_not_found_outcome(
                    visual_mode=visual_mode, semantic_visual_mode=semantic_visual_mode
                ),
            ),
            checkpoint=self._checkpoint(
                visual_mode=visual_mode, semantic_visual_mode=semantic_visual_mode
            ),
            policy=CapabilityPolicy(
                allowed_action_types=frozenset({"type", "click", "extract"}),
                allowed_entry_points=frozenset({entry_point}),
                maximum_risk=Risk.READ_ONLY,
                forbidden_text_inputs=("password", "security answer"),
                output_redaction={"member_id": "last4"},
            ),
            provenance=Provenance(
                discovery_run_id=run_id,
                provider=provider_name,
                model=model_name,
                prompt_policy_version="1.0.0",
                surface_adapter_version=self.surface_adapter_version,
                compiler_version=self.compiler_version,
                created_at=self.clock.now(),
                target_fingerprint=final_observation.fingerprint,
                evidence_manifest_key=evidence_manifest,
            ),
        )
        digest = artifact_content_hash(artifact)
        return artifact.model_copy(
            update={
                "provenance": artifact.provenance.model_copy(
                    update={"artifact_content_hash": digest}
                )
            }
        )

    @staticmethod
    def _validate_trace(steps: tuple[RecordedDiscoveryStep, ...]) -> None:
        if len(steps) < 8:
            raise CompilationError("trace is missing required workflow actions")
        first = steps[0].action
        if not (
            isinstance(first, TypeAction)
            and isinstance(first.value, InputValue)
            and first.value.path == "member_id"
        ):
            raise CompilationError("trace must begin with symbolic member_id input")
        if not isinstance(steps[1].action, ClickAction) or not isinstance(
            steps[2].action, ClickAction
        ):
            raise CompilationError("trace must search and open the savings account")
        extracted = {step.action.output for step in steps if isinstance(step.action, ExtractAction)}
        if extracted != set(_REQUIRED_OUTPUTS):
            raise CompilationError("trace must extract every declared output exactly")
        if any(step.risk is not Risk.READ_ONLY for step in steps):
            raise CompilationError("savings-balance trace must remain read-only")
        for step in steps:
            if step.target is None:
                raise CompilationError("every recorded action requires a stable target")
            if not step.target.visual_candidates and all(
                candidate.strategy is LocatorStrategy.COORDINATES
                for candidate in step.target.candidates
            ):
                raise CompilationError("coordinate-only targets cannot be published")

    @staticmethod
    def _compile_steps(
        recordings: tuple[RecordedDiscoveryStep, ...],
        *,
        visual_mode: bool,
        semantic_visual_mode: bool,
    ) -> tuple[Step, ...]:
        compiled: list[Step] = []
        for index, recording in enumerate(recordings):
            action = recording.action
            postconditions: tuple[Condition, ...]
            outcome_refs: tuple[str, ...]
            if isinstance(action, TypeAction):
                step_id = "search.enter_member_id"
                postconditions = ()
                outcome_refs = ()
            elif isinstance(action, ClickAction) and index == 1:
                step_id = "search.submit"
                postconditions = (
                    RenderedTextCondition(
                        kind="rendered_text", value="Member Results", match=MatchMode.EXACT
                    )
                    if semantic_visual_mode
                    else VisualTextCondition(
                        kind="visual_text", value="Member Results", match=MatchMode.EXACT
                    )
                    if visual_mode
                    else TextCondition(kind="text", value="Member Results", match=MatchMode.EXACT),
                )
                outcome_refs = ("member_not_found",)
            elif isinstance(action, ClickAction) and index == 2:
                step_id = "account.open_savings"
                postconditions = (
                    (
                        RenderedTextCondition(
                            kind="rendered_text",
                            value="Savings Account Details",
                            match=MatchMode.EXACT,
                        ),
                    )
                    if semantic_visual_mode
                    else (
                        VisualTextCondition(
                            kind="visual_text",
                            value="Savings Account Details",
                            match=MatchMode.EXACT,
                        ),
                    )
                    if visual_mode
                    else (RouteCondition(kind="route", pattern="/accounts/*/details"),)
                )
                outcome_refs = ()
            elif isinstance(action, ExtractAction):
                step_id = f"account.extract_{action.output}"
                postconditions = (OutputValidCondition(kind="output_valid", output=action.output),)
                outcome_refs = ()
            else:
                raise CompilationError("trace contains an unsupported action sequence")
            compiled.append(
                Step(
                    id=step_id,
                    name=step_id.replace(".", " ").replace("_", " ").title(),
                    action=action,
                    target=recording.target,
                    postconditions=postconditions,
                    timeout_ms=10_000,
                    retry=RetryPolicy(
                        max_attempts=2,
                        backoff_ms=(500,),
                        retry_on=("target_temporarily_absent",),
                        require_effect_absent=True,
                    ),
                    outcome_refs=outcome_refs,
                    risk=Risk.READ_ONLY,
                )
            )
        return tuple(compiled)

    @staticmethod
    def _inputs() -> ObjectContract:
        return ObjectContract(
            required=("member_id",),
            properties={
                "member_id": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Synthetic institution member identifier.",
                    data_classification=DataClassification.CUSTOMER_IDENTIFIER,
                    persistence=PersistenceMode.REDACTED,
                    pattern=r"^[0-9]{5,10}$",
                    min_length=5,
                    max_length=10,
                    example="12345",
                )
            },
        )

    @staticmethod
    def _outputs() -> ObjectContract:
        schemas = {
            "member_id": ValueSchema(
                type=JsonValueType.STRING,
                description="Member identifier shown on the detail page.",
                data_classification=DataClassification.CUSTOMER_IDENTIFIER,
                persistence=PersistenceMode.REDACTED,
                pattern=r"^[0-9]{5,10}$",
            ),
            "account_type": ValueSchema(
                type=JsonValueType.STRING,
                description="Selected account type.",
                data_classification=DataClassification.FINANCIAL,
                persistence=PersistenceMode.REDACTED,
                const="savings",
            ),
            "currency": ValueSchema(
                type=JsonValueType.STRING,
                description="ISO 4217 balance currency.",
                data_classification=DataClassification.FINANCIAL,
                persistence=PersistenceMode.REDACTED,
                enum=("USD",),
            ),
            "available_balance": ValueSchema(
                type=JsonValueType.STRING,
                description="Current available balance.",
                data_classification=DataClassification.FINANCIAL,
                persistence=PersistenceMode.REDACTED,
                format="decimal",
                pattern=r"^-?[0-9]+\.[0-9]{2}$",
            ),
            "as_of": ValueSchema(
                type=JsonValueType.STRING,
                description="Timestamp displayed by the target application.",
                data_classification=DataClassification.OPERATIONAL,
                persistence=PersistenceMode.FULL,
                format="date-time",
            ),
        }
        return ObjectContract(required=_REQUIRED_OUTPUTS, properties=schemas)

    @staticmethod
    def _member_not_found_outcome(
        *, visual_mode: bool = False, semantic_visual_mode: bool = False
    ) -> BusinessOutcome:
        return BusinessOutcome(
            code="member_not_found",
            description="The search completed and no matching member exists.",
            detect=AllCondition(
                kind="all",
                conditions=(
                    RenderedTextCondition(
                        kind="rendered_text", value="No member found", match=MatchMode.EXACT
                    )
                    if semantic_visual_mode
                    else VisualTextCondition(
                        kind="visual_text", value="No member found", match=MatchMode.EXACT
                    )
                    if visual_mode
                    else TextCondition(kind="text", value="No member found", match=MatchMode.EXACT),
                    RouteCondition(kind="route", pattern="/members/search"),
                ),
            ),
            allowed_after_steps=("search.submit",),
            result=OutcomeResult(
                details={"member_id": InputValue(source="input", path="member_id")}
            ),
        )

    @staticmethod
    def _checkpoint(*, visual_mode: bool = False, semantic_visual_mode: bool = False) -> Checkpoint:
        output_checks = tuple(
            OutputValidCondition(kind="output_valid", output=name) for name in _REQUIRED_OUTPUTS
        )
        return Checkpoint(
            id="savings_balance_verified",
            condition=AllCondition(
                kind="all",
                conditions=(
                    RenderedTextCondition(
                        kind="rendered_text",
                        value="Savings Account Details",
                        match=MatchMode.EXACT,
                    )
                    if semantic_visual_mode
                    else VisualTextCondition(
                        kind="visual_text", value="Savings Account Details", match=MatchMode.EXACT
                    )
                    if visual_mode
                    else RouteCondition(kind="route", pattern="/accounts/*/details"),
                    RenderedTextCondition(
                        kind="rendered_text", value="Savings", match=MatchMode.EXACT
                    )
                    if semantic_visual_mode
                    else VisualTextCondition(
                        kind="visual_text", value="Savings", match=MatchMode.EXACT
                    )
                    if visual_mode
                    else TextCondition(kind="text", value="Savings", match=MatchMode.EXACT),
                    *output_checks,
                    IdentityMatchesCondition(
                        kind="identity_matches",
                        extracted_output="member_id",
                        input_path="member_id",
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class TraceArtifactCompiler:
    """Compile any verified discovery trace from a provider-generated capability draft."""

    clock: Clock
    compiler_version: str = "2.0.0"
    surface_adapter_version: str = "web.v1"

    @property
    def output_contract(self) -> ObjectContract:
        # The discovery engine uses a planner for new runs. This empty contract keeps the port
        # explicit for callers that have not yet supplied a draft and fails closed on extraction.
        return ObjectContract(required=(), properties={})

    def compile_with_spec(
        self,
        *,
        draft: CapabilityDraftSpec,
        run_id: str,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        steps: tuple[RecordedDiscoveryStep, ...],
        final_observation: NormalizedObservation,
        provider_name: str,
        model_name: str,
        evidence_manifest: str,
        allowed_route_patterns: frozenset[str] | None = None,
        rendered_surface: bool = False,
        base_variant: str = "standard",
        surface_contract: str | None = None,
        maximum_risk: Risk | None = None,
        allowed_action_types: frozenset[str] | None = None,
        required_landmarks: tuple[Landmark, ...] = (),
        forbidden_landmarks: tuple[Landmark, ...] = (),
    ) -> CapabilityArtifact:
        self._validate_trace(draft, steps)
        completion_condition = _latest_surface_condition(steps)
        if completion_condition is None:
            raise CompilationError("trace completion lacks a verified surface-state condition")
        visual_mode = any(step.target and step.target.visual_candidates for step in steps)
        semantic_visual_mode = visual_mode and all(
            all(
                candidate.strategy
                in {
                    "rendered_text",
                    "rendered_labeled_control",
                    "rendered_field_value",
                    "rendered_group_image",
                }
                for candidate in step.target.visual_candidates
            )
            for step in steps
            if step.target is not None and step.target.visual_candidates
        )
        compiled_steps = tuple(
            self._compile_step(index, recording)
            for index, recording in enumerate(steps, start=1)
        )
        risk = max(
            (draft.risk, *(recording.risk for recording in steps)),
            key=lambda value: RISK_RANK[value],
        )
        if maximum_risk is not None and RISK_RANK[risk] > RISK_RANK[maximum_risk]:
            raise CompilationError("trace risk exceeds the registered policy ceiling")
        if allowed_action_types is not None and any(
            step.action.kind not in allowed_action_types for step in steps
        ):
            raise CompilationError("trace contains an action outside the registered policy")
        observed_routes = tuple(
            route
            for recording in steps
            for route in (
                recording.observation_before.route,
                recording.observation_after.route,
            )
            if route.startswith("/")
        )
        if final_observation.route.startswith("/"):
            observed_routes += (final_observation.route,)
        route_patterns = frozenset(
            self._narrowest_route_pattern(route, allowed_route_patterns)
            for route in observed_routes
        )
        starting_route = self._narrowest_route_pattern(
            steps[0].observation_before.route, allowed_route_patterns
        )
        final_route = self._narrowest_route_pattern(final_observation.route, allowed_route_patterns)
        observed_landmarks = {
            landmark
            for recording in steps
            for landmark in (
                *recording.observation_before.landmarks,
                *recording.observation_after.landmarks,
            )
        }
        observed_landmarks.update(final_observation.landmarks)
        if required_landmarks and any(
            landmark.value not in observed_landmarks for landmark in required_landmarks
        ):
            raise CompilationError("registered readiness landmarks were not confirmed")
        if forbidden_landmarks and any(
            landmark.value in observed_landmarks for landmark in forbidden_landmarks
        ):
            raise CompilationError("trace observed a forbidden application landmark")
        fingerprint_landmarks = required_landmarks or (
            Landmark(
                kind="visual_text" if semantic_visual_mode else "heading",
                value=next(iter(steps[0].observation_before.landmarks), "Application surface"),
            ),
        )
        output_checks = tuple(
            OutputValidCondition(kind="output_valid", output=name)
            for name in draft.outputs.required
        )
        checkpoint_conditions: tuple[Condition, ...] = (
            RouteCondition(kind="route", pattern=final_route),
            completion_condition,
            *output_checks,
        )
        artifact = CapabilityArtifact(
            schema_version="1.4",
            capability=CapabilityMetadata(
                id=draft.capability_id or f"{application_family}.{draft.operation_slug}",
                version="1.0.0",
                name=draft.name,
                description=draft.description or goal.strip(),
                application_family=application_family,
                surface=SurfaceKind.WEB,
                risk=risk,
                tags=draft.tags,
            ),
            compatibility=Compatibility(
                application_family=application_family,
                base_variant=base_variant,
                supported_variants=(tenant,),
                surface_contract=surface_contract or self.surface_adapter_version,
                entry_point=entry_point,
                rendered_surface=rendered_surface,
                fingerprint=SurfaceFingerprint(
                    required_landmarks=fingerprint_landmarks,
                    forbidden_landmarks=forbidden_landmarks,
                ),
            ),
            inputs=draft.inputs,
            outputs=draft.outputs,
            preconditions=(
                RouteCondition(kind="route", pattern=starting_route),
            ),
            steps=compiled_steps,
            checkpoint=Checkpoint(
                id=f"{draft.operation_slug}_verified",
                condition=AllCondition(kind="all", conditions=checkpoint_conditions),
            ),
            policy=CapabilityPolicy(
                allowed_action_types=frozenset(step.action.kind for step in compiled_steps),
                allowed_entry_points=frozenset({entry_point}),
                maximum_risk=risk,
                allowed_route_patterns=route_patterns,
                output_redaction={
                    name: "last4"
                    for name, schema in draft.outputs.properties.items()
                    if schema.persistence is PersistenceMode.REDACTED
                },
            ),
            provenance=Provenance(
                discovery_run_id=run_id,
                provider=provider_name,
                model=model_name,
                prompt_policy_version="2.0.0",
                surface_adapter_version=self.surface_adapter_version,
                compiler_version=self.compiler_version,
                created_at=self.clock.now(),
                target_fingerprint=final_observation.fingerprint,
                evidence_manifest_key=evidence_manifest,
            ),
        )
        digest = artifact_content_hash(artifact)
        return artifact.model_copy(
            update={
                "provenance": artifact.provenance.model_copy(
                    update={"artifact_content_hash": digest}
                )
            }
        )

    def compile(self, **kwargs: object) -> CapabilityArtifact:
        draft = kwargs.pop("draft", None)
        if not isinstance(draft, CapabilityDraftSpec):
            raise CompilationError("a capability draft is required for generic compilation")
        return self.compile_with_spec(draft=draft, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _narrowest_route_pattern(route: str, patterns: frozenset[str] | None) -> str:
        if not route.startswith("/"):
            raise CompilationError("trace contains an invalid observed route")
        if not patterns:
            return route
        matches = [pattern for pattern in patterns if _route_matches(route, pattern)]
        if not matches:
            raise CompilationError("trace observed a route outside the registered policy")
        return max(matches, key=_route_specificity)

    @staticmethod
    def _validate_trace(
        draft: CapabilityDraftSpec, steps: tuple[RecordedDiscoveryStep, ...]
    ) -> None:
        if not steps:
            raise CompilationError("trace contains no actions")
        input_names = set(draft.inputs.properties)
        extracted: set[str] = set()
        for recording in steps:
            action = recording.action
            if recording.target is None and action.kind in {"click", "type", "select", "extract"}:
                raise CompilationError("every target action requires a stable target")
            if (
                isinstance(action, TypeAction | SelectAction)
                and isinstance(
                    action.value if isinstance(action, TypeAction) else action.option,
                    InputValue,
                )
            ):
                source = action.value if isinstance(action, TypeAction) else action.option
                assert isinstance(source, InputValue)
                if source.path.split(".", 1)[0] not in input_names:
                    raise CompilationError("trace references an undeclared input")
            if isinstance(action, ExtractAction):
                if action.output not in draft.outputs.properties:
                    raise CompilationError("trace extracts an undeclared output")
                if action.output in extracted:
                    raise CompilationError("trace extracts an output more than once")
                extracted.add(action.output)
            literal_source = (
                action.value if isinstance(action, TypeAction) else action.option
                if isinstance(action, SelectAction)
                else None
            )
            if (
                isinstance(literal_source, LiteralValue)
                and isinstance(literal_source.value, str)
                and literal_source.value.isdigit()
            ):
                raise CompilationError("trace contains a literal customer value")
            if (
                recording.target is not None
                and not recording.target.visual_candidates
                and recording.target.candidates
                and all(
                    candidate.strategy is LocatorStrategy.COORDINATES
                    for candidate in recording.target.candidates
                )
            ):
                raise CompilationError("coordinate-only targets cannot be published")
        missing = set(draft.outputs.required) - extracted
        if missing:
            raise CompilationError(f"trace is missing required outputs: {sorted(missing)}")

    @staticmethod
    def _compile_step(index: int, recording: RecordedDiscoveryStep) -> Step:
        action = recording.action
        raw = recording.target.description if recording.target is not None else action.kind
        slug = "_".join(part for part in raw.lower().replace("-", " ").split() if part.isalnum())
        slug = slug[:40] or action.kind
        postconditions: tuple[Condition, ...] = recording.verified_postconditions or (
            (OutputValidCondition(kind="output_valid", output=action.output),)
            if isinstance(action, ExtractAction)
            else ()
        )
        return Step(
            id=f"step_{index:02d}_{action.kind}_{slug}",
            name=raw,
            action=action,
            target=recording.target,
            postconditions=postconditions,
            timeout_ms=10_000,
            retry=RetryPolicy(
                max_attempts=2,
                backoff_ms=(500,),
                retry_on=("target_temporarily_absent",),
                require_effect_absent=True,
            ),
            risk=recording.risk,
        )


def _route_matches(route: str, pattern: str) -> bool:
    if not route.startswith("/") or "?" in route or "#" in route:
        return False
    route_parts = route.strip("/").split("/") if route != "/" else []
    pattern_parts = pattern.strip("/").split("/") if pattern != "/" else []
    if len(route_parts) != len(pattern_parts):
        return False
    return all(
        pattern_part in {"*"} or pattern_part.startswith(":")
        or pattern_part == route_part
        for route_part, pattern_part in zip(route_parts, pattern_parts, strict=True)
    )


def _route_specificity(pattern: str) -> tuple[int, int]:
    parts = pattern.strip("/").split("/") if pattern != "/" else []
    static = sum(not (part == "*" or part.startswith(":")) for part in parts)
    return static, len(parts)


def _surface_conditions(condition: Condition) -> tuple[Condition, ...]:
    if condition.kind in {"route", "text", "rendered_text", "visual_text", "element"}:
        return (condition,)
    if isinstance(condition, AllCondition | AnyCondition):
        return tuple(
            nested
            for item in condition.conditions
            for nested in _surface_conditions(item)
        )
    if isinstance(condition, NotCondition):
        nested = _surface_conditions(condition.condition)
        return (condition,) if nested else ()
    return ()


def _latest_surface_condition(
    steps: tuple[RecordedDiscoveryStep, ...],
) -> Condition | None:
    """Return the latest condition that was actually verified during discovery."""

    for recording in reversed(steps):
        for condition in reversed(recording.verified_postconditions):
            surface_conditions = _surface_conditions(condition)
            if surface_conditions:
                return condition
    return None


# Existing reviewed fixtures and tests keep this name while the runtime uses TraceArtifactCompiler.
SavingsBalanceCompiler = LegacySavingsBalanceCompiler
