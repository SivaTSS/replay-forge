"""Historical savings compiler retained only for regression fixtures."""

from __future__ import annotations

from dataclasses import dataclass

from replayforge.capabilities.models import (
    AllCondition,
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
    LocatorStrategy,
    MatchMode,
    ObjectContract,
    OutcomeResult,
    OutputValidCondition,
    PersistenceMode,
    Provenance,
    RenderedTextCondition,
    RetryPolicy,
    RouteCondition,
    Step,
    SurfaceFingerprint,
    SurfaceKind,
    TextCondition,
    TypeAction,
    ValueSchema,
    VisualTextCondition,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.discovery.compiler import CompilationError
from replayforge.discovery.models import RecordedDiscoveryStep
from replayforge.policy.types import DataClassification, Risk
from replayforge.shared.clock import Clock
from replayforge.surfaces.models import NormalizedObservation

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


SavingsBalanceCompiler = LegacySavingsBalanceCompiler
