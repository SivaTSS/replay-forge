"""Task-independent compiler for verified discovery traces."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    CapabilityArtifact,
    CapabilityMetadata,
    CapabilityPolicy,
    Checkpoint,
    Compatibility,
    Condition,
    ExtractAction,
    InputValue,
    Landmark,
    LiteralValue,
    LocatorStrategy,
    MatchMode,
    NotCondition,
    ObjectContract,
    OutputValidCondition,
    PersistenceMode,
    Provenance,
    RenderedFieldValueCandidate,
    RenderedTextCondition,
    RetryPolicy,
    RouteCondition,
    SelectAction,
    Step,
    SurfaceFingerprint,
    SurfaceKind,
    TypeAction,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.discovery.models import CapabilityDraftSpec, RecordedDiscoveryStep
from replayforge.policy.types import RISK_RANK, Risk
from replayforge.shared.clock import Clock
from replayforge.surfaces.models import NormalizedObservation


class CompilationError(ValueError):
    """The successful trace is insufficient for deterministic compilation."""


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
                    "input_text",
                    "rendered_text",
                    "rendered_labeled_control",
                    "rendered_field_value",
                    "rendered_group_image",
                }
                or (
                    candidate.strategy == "ocr_relative"
                    and candidate.target_text is not None
                    and candidate.relative_region is None
                    and candidate.search_region is None
                )
                for candidate in step.target.visual_candidates
            )
            for step in steps
            if step.target is not None and step.target.visual_candidates
        )
        compiled_steps = tuple(
            self._compile_step(index, recording) for index, recording in enumerate(steps, start=1)
        )
        # Capability risk describes effects that were actually executed and policy-evaluated.
        # The planning model's conservative task guess must not upgrade an observed read-only
        # trace into a sensitive capability.
        risk = max((recording.risk for recording in steps), key=lambda value: RISK_RANK[value])
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
            preconditions=(RouteCondition(kind="route", pattern=starting_route),),
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
            if isinstance(action, TypeAction | SelectAction) and isinstance(
                action.value if isinstance(action, TypeAction) else action.option,
                InputValue,
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
                action.value
                if isinstance(action, TypeAction)
                else action.option
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
        pattern_part in {"*"} or pattern_part.startswith(":") or pattern_part == route_part
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
            nested for item in condition.conditions for nested in _surface_conditions(item)
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
    # Resolving and extracting a rendered labeled value verifies that its structural
    # label exists exactly once. Use the full set of those verified labels as the
    # completion signature when the provider did not propose a separate assertion.
    labels: list[str] = []
    for recording in steps:
        if not isinstance(recording.action, ExtractAction) or recording.target is None:
            continue
        for candidate in recording.target.visual_candidates:
            if isinstance(candidate, RenderedFieldValueCandidate) and candidate.label not in labels:
                labels.append(candidate.label)
                break
    if labels:
        conditions = tuple(
            RenderedTextCondition(kind="rendered_text", value=label, match=MatchMode.EXACT)
            for label in labels
        )
        return (
            conditions[0]
            if len(conditions) == 1
            else AllCondition(kind="all", conditions=conditions)
        )
    return None
