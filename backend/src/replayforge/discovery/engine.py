"""Bounded observe-decide-act orchestration for capability discovery."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

from replayforge.capabilities.conditions import proves_distinct_surface
from replayforge.capabilities.models import (
    AssertAction,
    CapabilityArtifact,
    ExtractAction,
    InputValue,
    Landmark,
    LiteralValue,
    ObjectContract,
    OutputValidCondition,
    SelectAction,
    TypeAction,
    WaitForAction,
)
from replayforge.capabilities.targeting import bind_target_inputs, target_input_paths
from replayforge.capabilities.transforms import transform_extracted_text
from replayforge.capabilities.values import (
    ContractValidationError,
    binding_classification,
    resolve_input,
    validate_object,
)
from replayforge.discovery.conditions import validate_condition_bindings
from replayforge.discovery.constraints import (
    DEFAULT_DISCOVERY_STEPS,
    DEFAULT_DISCOVERY_TIMEOUT,
    MAX_DISCOVERY_STEPS,
    MAX_DISCOVERY_TIMEOUT,
    MIN_DISCOVERY_STEPS,
    MIN_DISCOVERY_TIMEOUT,
)
from replayforge.discovery.models import (
    ActProposal,
    BranchProposal,
    CapabilityDraftSpec,
    CompleteProposal,
    DiscoveryProposal,
    DiscoveryResult,
    DiscoverySuccess,
    EscalateProposal,
    ObservedBranch,
    PlanningContext,
    ProviderContext,
    RecordedActionProposal,
    RecordedDiscoveryStep,
    ScenarioContext,
)
from replayforge.discovery.ports import ArtifactCompiler, ModelProvider, ModelProviderError
from replayforge.discovery.privacy import (
    ArtifactPrivacyError,
    extraction_locator_contains_value,
    redact_contract_descriptions,
    target_contains_invocation_literal,
    validate_artifact_privacy,
)
from replayforge.discovery.scenarios import scenario_expected_condition
from replayforge.evidence.models import RetentionClass, SanitizedEvidence
from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.interventions.leases import (
    ControlLeaseService,
    LeaseConflictError,
    LeaseExpiredError,
)
from replayforge.interventions.models import (
    AUTOMATION_OWNER,
    InterventionContext,
    InterventionRunMode,
)
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import (
    ActionContext,
    EffectivePolicy,
    PrincipalType,
    RunMode,
)
from replayforge.policy.types import Decision, Risk
from replayforge.runs.ports import InterventionRouter, RunRecorder
from replayforge.runs.results import (
    ArtifactPrivacyDiagnostic,
    FailureResult,
    InterventionRequiredResult,
)
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import ActionStatus, NormalizedObservation, SurfaceError
from replayforge.surfaces.ports import SurfaceDriver, SurfaceSession


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    run_id: str
    goal: str
    application_family: str
    tenant: str
    entry_point: str
    inputs: dict[str, Any]
    existing_capability_id: str | None = None
    scenario: ScenarioContext | None = None
    max_steps: int = DEFAULT_DISCOVERY_STEPS
    timeout: timedelta = DEFAULT_DISCOVERY_TIMEOUT
    max_repeated_state: int = 2
    max_repeated_action: int = 2
    minimum_confidence: float = 0.6

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("discovery goal is required")
        if not MIN_DISCOVERY_STEPS <= self.max_steps <= MAX_DISCOVERY_STEPS:
            raise ValueError("discovery step budget must be between 1 and 50")
        if not MIN_DISCOVERY_TIMEOUT <= self.timeout <= MAX_DISCOVERY_TIMEOUT:
            raise ValueError("discovery timeout must be between 10 and 600 seconds")
        if self.max_repeated_state < 1 or self.max_repeated_action < 1:
            raise ValueError("stuck-detection limits must be positive")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("minimum confidence must be between zero and one")
        if self.existing_capability_id is not None and not self.existing_capability_id.strip():
            raise ValueError("existing capability id cannot be blank")


@dataclass(frozen=True, slots=True)
class DiscoveryPause:
    request: DiscoveryRequest
    session: SurfaceSession
    policy: EffectivePolicy
    observation: NormalizedObservation
    lease_version: int
    code: str
    step_id: str


@dataclass(frozen=True, slots=True)
class DiscoveryContinuation:
    loop: Generator[DiscoveryPause, int, DiscoveryResult]
    pause: DiscoveryPause


class DiscoveryBlockedError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DiscoveryEngine:
    surface_driver: SurfaceDriver
    model_provider: ModelProvider
    artifact_compiler: ArtifactCompiler
    policy_evaluator: PolicyEvaluator
    effective_policy: EffectivePolicy | None
    lease_service: ControlLeaseService
    recorder: RunRecorder
    clock: Clock
    policy_resolver: Callable[[DiscoveryRequest], EffectivePolicy] | None = None
    contract_planner: Callable[[PlanningContext], CapabilityDraftSpec] | None = None
    capability_id_resolver: Callable[[str, str], str] | None = None
    privacy_redactor: StructuredRedactor = field(default_factory=StructuredRedactor)
    intervention_router: InterventionRouter | None = None
    _continuations: dict[str, DiscoveryContinuation] = field(default_factory=dict, repr=False)

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult | InterventionRequiredResult:
        return self._advance(self._run(request))

    def _advance(
        self, loop: Generator[DiscoveryPause, int, DiscoveryResult], version: int | None = None
    ) -> DiscoveryResult | InterventionRequiredResult:
        try:
            pause = next(loop) if version is None else loop.send(version)
        except StopIteration as completed:
            return cast(DiscoveryResult, completed.value)
        if self.intervention_router is None:
            loop.close()
            return self._failure(pause.request, pause.code, "Discovery is blocked.")
        try:
            frame = pause.session.capture_sanitized_evidence_frame()
            self.recorder.attach_sanitized(
                "discovery-handoff-before",
                SanitizedEvidence(frame.content, "image/png", frame.redaction_directives),
                RetentionClass.HUMAN_AUDIT,
            )
            intervention_id = str(new_id(EntityKind.INTERVENTION))
            self.intervention_router.open(
                intervention_id=intervention_id,
                run_id=pause.request.run_id,
                session_id=pause.session.session_id,
                expected_lease_version=pause.lease_version,
                code=pause.code,
                step_id=pause.step_id,
                observation=pause.observation,
                explanation="Discovery is blocked; correct the live state before resuming.",
                context=InterventionContext(
                    run_mode=InterventionRunMode.DISCOVERY,
                    application_family=pause.request.application_family,
                    tenant=pause.request.tenant,
                    task_summary=pause.request.goal,
                    surface_route=pause.observation.route,
                    step_id=pause.step_id,
                ),
            )
            self._continuations[intervention_id] = DiscoveryContinuation(loop, pause)
            self.recorder.record(
                "intervention_required",
                pause.request.run_id,
                step_id=pause.step_id,
                details={"code": pause.code},
            )
            return InterventionRequiredResult(
                status="intervention_required",
                run_id=pause.request.run_id,
                intervention_id=intervention_id,
                code=pause.code,
                step_id=pause.step_id,
                session_live=True,
                control_owner="automation_paused",
            )
        except BaseException:
            loop.close()
            raise

    def validate_resume(self, intervention_id: str) -> None:
        pause = self._continuations[intervention_id].pause
        observation = pause.session.observe()
        if not self.policy_evaluator.location_allowed(
            pause.policy, pause.session.origin, observation.route
        ):
            raise SurfaceError(
                "resume_location_not_allowed", "The session is outside its allowed location."
            )
        if observation.fingerprint == pause.observation.fingerprint:
            raise SurfaceError(
                "resume_state_unchanged", "The blocked discovery state has not changed."
            )
        frame = pause.session.capture_sanitized_evidence_frame()
        self.recorder.attach_sanitized(
            "discovery-handoff-after",
            SanitizedEvidence(frame.content, "image/png", frame.redaction_directives),
            RetentionClass.HUMAN_AUDIT,
        )
        self.recorder.record("resume_checkpoint_verified", pause.request.run_id)

    def resume(
        self, intervention_id: str, version: int
    ) -> DiscoveryResult | InterventionRequiredResult:
        continuation = self._continuations.pop(intervention_id)
        return self._advance(continuation.loop, version)

    def cancel(self, intervention_id: str) -> FailureResult:
        continuation = self._continuations.pop(intervention_id)
        continuation.loop.close()
        return self._failure(
            continuation.pause.request,
            "discovery_terminated",
            "Discovery was terminated while paused.",
        )

    def _run(self, request: DiscoveryRequest) -> Generator[DiscoveryPause, int, DiscoveryResult]:
        session: SurfaceSession | None = None
        started_at = self.clock.now()
        try:
            if self.policy_resolver is not None:
                effective_policy = self.policy_resolver(request)
            elif self.effective_policy is not None:
                effective_policy = self.effective_policy
            else:
                raise RuntimeError("discovery policy resolver is not configured")
            session = self.surface_driver.open(
                request.application_family, request.tenant, request.entry_point
            )
            lease = self.lease_service.create_for_automation(session.session_id)

            def renew_control() -> None:
                nonlocal lease
                lease = self.lease_service.heartbeat(
                    session.session_id, lease.version, AUTOMATION_OWNER
                )

            self.recorder.record("discovery_started", request.run_id)
            draft = self._plan_contract(request, session, effective_policy, renew_control)
            output_contract = (
                draft.outputs if draft is not None else self.artifact_compiler.output_contract
            )
            recordings: list[RecordedDiscoveryStep] = []
            observed_branch: ObservedBranch | None = None
            history: list[str] = []
            outputs: dict[str, Any] = {}
            previous_fingerprint: str | None = None
            repeated_state = 0
            repeated_action = 0
            previous_action: str | None = None

            def handoff(
                code: str, current: NormalizedObservation
            ) -> Generator[DiscoveryPause, int, None]:
                nonlocal started_at, lease, previous_fingerprint, previous_action
                nonlocal repeated_state, repeated_action
                assert session is not None
                paused_at = self.clock.now()
                version = yield DiscoveryPause(
                    request,
                    session,
                    effective_policy,
                    current,
                    lease.version,
                    code,
                    f"discovery_step_{_step_number}",
                )
                # Human wait is excluded; used model calls and automation steps are not reset.
                started_at += self.clock.now() - paused_at
                lease = self.lease_service.heartbeat(session.session_id, version, AUTOMATION_OWNER)
                previous_fingerprint = previous_action = None
                repeated_state = repeated_action = 0
                outputs.clear()  # Human edits may invalidate previously extracted values.
                history.append(
                    "A human corrected the blocked live session. Reobserve and re-extract outputs. "
                    "Manual actions are audit evidence, not recorded automation steps. "
                    "The recorded program must still pass fresh deterministic replay."
                )

            for _step_number in range(1, request.max_steps + 1):
                if self.clock.now() - started_at >= request.timeout:
                    return self._failure(request, "discovery_timeout", "Time budget exhausted.")
                lease = self.lease_service.heartbeat(
                    session.session_id, lease.version, AUTOMATION_OWNER
                )
                observation = session.observe()
                self.recorder.record("observation_captured", request.run_id)
                previous_was_observational = bool(recordings) and isinstance(
                    recordings[-1].action, ExtractAction | AssertAction | WaitForAction
                )
                if (
                    observation.fingerprint == previous_fingerprint
                    and not previous_was_observational
                ):
                    repeated_state += 1
                else:
                    repeated_state = 0
                previous_fingerprint = observation.fingerprint
                if repeated_state >= request.max_repeated_state:
                    yield from handoff("repeated_observation", observation)
                    continue

                screenshot_png = session.capture_provider_frame()
                renew_control()
                proposal = self.model_provider.decide(
                    ProviderContext(
                        goal=request.goal,
                        inputs=request.inputs,
                        observation=observation,
                        screenshot_png=screenshot_png,
                        action_history=tuple(history),
                        allowed_action_types=effective_policy.allowed_action_types,
                        rendered_surface=bool(getattr(session, "rendered_surface", False)),
                        output_contract=output_contract,
                        captured_output_names=tuple(
                            name for name in output_contract.properties if name in outputs
                        ),
                        maximum_risk=effective_policy.maximum_risk,
                        previous_visual_text=(
                            tuple(
                                token.text
                                for token in recordings[-1].observation_before.visual_tokens
                            )
                            if recordings
                            else ()
                        ),
                        reference_steps=(
                            request.scenario.primary.steps if request.scenario else ()
                        ),
                        scenario_kind=request.scenario.kind if request.scenario else None,
                        branch_observed=observed_branch is not None,
                        recorded_step_count=len(recordings),
                        recovery_resume_before=(
                            request.scenario.primary.steps[observed_branch.after_step_count]
                            if request.scenario is not None
                            and request.scenario.kind == "recovery"
                            and observed_branch is not None
                            and observed_branch.after_step_count
                            < len(request.scenario.primary.steps)
                            else None
                        ),
                    )
                )
                renew_control()
                self.recorder.record(
                    "model_proposal_received",
                    request.run_id,
                    details=self._proposal_summary(proposal),
                )
                if isinstance(proposal, EscalateProposal):
                    yield from handoff(proposal.reason_code, observation)
                    continue
                if isinstance(proposal, CompleteProposal):
                    if request.scenario is not None and observed_branch is None:
                        return self._failure(
                            request,
                            "scenario_branch_missing",
                            "Scenario completion requires a verified branch marker.",
                        )
                    if (
                        request.scenario is not None
                        and request.scenario.kind == "recovery"
                        and observed_branch is not None
                        and (
                            not any(
                                proves_distinct_surface(condition, observed_branch.condition)
                                for recorded in recordings[observed_branch.after_step_count + 1 :]
                                for condition in recorded.verified_postconditions
                            )
                            or not self._rejoin_ready(
                                request, session, observed_branch, outputs, effective_policy
                            )
                        )
                    ):
                        renew_control()
                        self.recorder.record(
                            "proposal_rejected",
                            request.run_id,
                            details={"code": "recovery_rejoin_not_ready", "effect_absent": True},
                        )
                        history.append(
                            "Completion rejected: recovery requires executed corrective steps, "
                            "a distinct verified restored-state condition, and readiness of the "
                            "exact next primary target/preconditions. A branch marker or "
                            "acknowledgement alone is insufficient. "
                            "Restore the original surface, including scrolling when necessary; "
                            "do not click the rejoin target. Assert a distinctive condition on the "
                            "currently visible restored surface before completing again."
                        )
                        continue
                    return self._complete(
                        request,
                        session,
                        recordings,
                        observation,
                        draft,
                        effective_policy,
                        outputs,
                        observed_branch,
                    )

                pending_branch: ObservedBranch | None = None
                if isinstance(proposal, RecordedActionProposal):
                    reference = (
                        next(
                            (
                                step
                                for step in request.scenario.primary.steps
                                if step.id == proposal.step_id
                            ),
                            None,
                        )
                        if request.scenario
                        else None
                    )
                    if reference is None:
                        return self._failure(
                            request,
                            "scenario_reference_invalid",
                            "The requested recorded action is unavailable.",
                        )
                    proposal = ActProposal(
                        kind="act",
                        action=reference.action,
                        target=reference.target,
                        rationale=proposal.rationale,
                        expected_effect="Reobserve the recorded action's effect.",
                        expected_condition=scenario_expected_condition(
                            reference, proposal.expected_condition
                        ),
                        declared_risk=reference.risk,
                        confidence=1.0,
                    )
                elif isinstance(proposal, BranchProposal):
                    if request.scenario is None or observed_branch is not None or not recordings:
                        return self._failure(
                            request,
                            "scenario_branch_invalid",
                            "A scenario requires one branch after an executed prefix.",
                        )
                    pending_branch = ObservedBranch(len(recordings), proposal.condition)
                    proposal = ActProposal(
                        kind="act",
                        action=AssertAction(kind="assert", condition=proposal.condition),
                        rationale=proposal.rationale,
                        expected_effect="Verify the observed branch.",
                        expected_condition=proposal.condition,
                        declared_risk=Risk.READ_ONLY,
                        confidence=1.0,
                    )

                action_fingerprint = self._operation_fingerprint(proposal)
                repeated_action = (
                    repeated_action + 1 if action_fingerprint == previous_action else 0
                )
                previous_action = action_fingerprint
                if repeated_action >= request.max_repeated_action:
                    yield from handoff("repeated_action", observation)
                    continue
                if proposal.confidence < request.minimum_confidence:
                    yield from handoff("low_model_confidence", observation)
                    continue

                try:
                    act_result = self._act(
                        request,
                        session,
                        renew_control,
                        proposal,
                        observation,
                        outputs,
                        effective_policy,
                        output_contract,
                        draft.inputs if draft is not None else None,
                    )
                except DiscoveryBlockedError as error:
                    yield from handoff(error.code, observation)
                    continue
                except SurfaceError as error:
                    if not error.recoverable or not error.effect_absent:
                        if error.intervention_recommended:
                            yield from handoff(error.code, session.observe())
                            continue
                        raise
                    self.recorder.record(
                        "proposal_rejected",
                        request.run_id,
                        details={"code": error.code, "effect_absent": True},
                    )
                    # A safely rejected proposal made no UI progress and should not consume
                    # the repeated-observation allowance for the next replanning attempt.
                    previous_fingerprint = None
                    repeated_state = 0
                    if error.code == "target_ambiguous":
                        history.append(
                            "Rejected proposal (not executed): "
                            + self._proposal_fingerprint(proposal)
                            + "\nPrevious proposal was not executed (target_ambiguous). "
                            "Choose a different uniquely identifiable target or anchor from the "
                            "current screenshot. The relation across every matching anchor must "
                            "identify one target; repeating this locator cannot resolve ambiguity. "
                            "Escalate if no supported locator can distinguish the control."
                        )
                    elif error.code == "visual_select_unsupported":
                        history.append(
                            "Previous proposal was not executed: select requires a native DOM "
                            "select element. For a rendered/custom dropdown, open the visible "
                            "control and choose the observed option with click or supported keys. "
                            "Use input_text for an option bound to invocation data."
                        )
                    elif error.code == "literal_input_target":
                        history.append(
                            "Previous proposal was not executed: invocation data appeared in its "
                            "target. Use input_text with the relevant symbolic input path; "
                            "optionally add the observed control text and its row/column relation."
                        )
                    elif error.code == "extraction_locator_value_bound":
                        history.append(
                            "Previous extraction was rejected and its output was not bound: "
                            "the locator contains the value being extracted. Use a stable field "
                            "label with rendered_field_value (or a structural DOM field locator), "
                            "not the displayed customer, financial, date, or reference value."
                        )
                    elif error.code in {"condition_output_unbound", "condition_input_unbound"}:
                        history.append(
                            "Rejected proposal (not executed): "
                            + self._proposal_fingerprint(proposal)
                            + f"\nCondition binding rejected ({error.code}). "
                            "Extract the referenced required output before asserting or waiting "
                            "on it, and use an available symbolic input path. Seeing text on "
                            "screen does not bind an output. Captured and remaining output "
                            "fields describe the actual runtime bindings."
                        )
                    elif error.code == "risk_classification_unresolved":
                        history.append(
                            "Previous proposal was not executed: if the screen visibly proves an "
                            "explicit inverse, retry with reversible risk; otherwise escalate."
                        )
                    else:
                        history.append(
                            f"Previous proposal was not executed ({error.code}); "
                            "choose a different safe target."
                        )
                    continue
                if isinstance(act_result, FailureResult):
                    return act_result
                recording, history_item = act_result
                recordings.append(recording)
                if pending_branch is not None:
                    observed_branch = pending_branch
                    self.recorder.record(
                        "branch_observed",
                        request.run_id,
                        details={
                            "after_step_count": observed_branch.after_step_count,
                            "condition_kind": observed_branch.condition.kind,
                        },
                    )
                    if request.scenario is not None and request.scenario.kind != "recovery":
                        # A verified terminal marker ends this trace. Further model actions
                        # could change the rejected request or mutate a negative result.
                        renew_control()
                        return self._complete(
                            request,
                            session,
                            recordings,
                            recording.observation_after,
                            draft,
                            effective_policy,
                            outputs,
                            observed_branch,
                        )
                history.append(history_item)

            return self._failure(request, "max_steps_exceeded", "Discovery step budget exhausted.")
        except ModelProviderError as error:
            return self._failure(request, error.code, error.safe_message)
        except SurfaceError as error:
            return self._failure(request, error.code, error.safe_message)
        except LeaseExpiredError:
            return self._failure(request, "control_lease_expired", "Automation control expired.")
        except LeaseConflictError:
            return self._failure(request, "control_lease_conflict", "Automation ownership changed.")
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _rejoin_ready(
        request: DiscoveryRequest,
        session: SurfaceSession,
        branch: ObservedBranch,
        outputs: dict[str, Any],
        policy: EffectivePolicy,
    ) -> bool:
        assert request.scenario is not None
        primary = request.scenario.primary
        if branch.after_step_count >= len(primary.steps):
            return False
        step = primary.steps[branch.after_step_count]
        try:
            if step.target is not None:
                target = bind_target_inputs(
                    step.target, request.inputs, primary.inputs, policy.forbidden_field_classes
                )
                session.resolve(target, step.timeout_ms)
            return all(
                session.evaluate(item, outputs, request.inputs) for item in step.preconditions
            )
        except (SurfaceError, ContractValidationError):
            return False

    def _complete(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        recordings: list[RecordedDiscoveryStep],
        observation: NormalizedObservation,
        draft: CapabilityDraftSpec | None,
        effective_policy: EffectivePolicy,
        outputs: dict[str, Any],
        observed_branch: ObservedBranch | None,
    ) -> DiscoverySuccess | FailureResult:
        try:
            artifact = self._compile(
                request,
                recordings,
                observation,
                draft,
                effective_policy,
                bool(getattr(session, "rendered_surface", False)),
                str(getattr(session, "base_variant", "standard")),
                str(getattr(session, "surface_contract", "web.v1")),
                tuple(getattr(session, "required_landmarks", ())),
                tuple(getattr(session, "forbidden_landmarks", ())),
            )
            artifact = redact_contract_descriptions(
                artifact, request.inputs, outputs, self.privacy_redactor
            )
            validate_artifact_privacy(artifact, request.inputs, self.privacy_redactor, outputs)
        except ArtifactPrivacyError as error:
            return self._failure(
                request,
                "artifact_privacy_rejected",
                str(error),
                privacy_rejection=ArtifactPrivacyDiagnostic(
                    source=error.source, location=error.location
                ),
            )
        except EvidenceRejectedError:
            return self._failure(
                request,
                "artifact_privacy_rejected",
                "The compiled trace contains private or forbidden data.",
            )
        except ValueError:
            return self._failure(
                request,
                "artifact_compilation_failed",
                "The observed trace could not be compiled into a safe capability.",
            )
        if not session.wait_until(artifact.checkpoint.condition, outputs, request.inputs, 10_000):
            return self._failure(
                request,
                "completion_not_verified",
                "Completion lacked deterministic checkpoint evidence.",
            )
        try:
            validate_object(artifact.outputs, outputs)
        except ContractValidationError as error:
            return self._failure(request, "completion_output_invalid", str(error))
        self.recorder.record("artifact_compiled", request.run_id)
        return DiscoverySuccess(
            status="success",
            run_id=request.run_id,
            artifact=artifact,
            evidence_manifest=self.recorder.evidence_manifest_key,
            branch=observed_branch,
        )

    def _plan_contract(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        effective_policy: EffectivePolicy,
        renew_control: Callable[[], None],
    ) -> CapabilityDraftSpec | None:
        if request.scenario is not None:
            primary = request.scenario.primary
            if (
                primary.capability.application_family != request.application_family
                or primary.compatibility.entry_point != request.entry_point
            ):
                raise ModelProviderError(
                    "scenario_target_invalid", "Scenario target differs from its primary."
                )
            try:
                validate_object(primary.inputs, request.inputs)
            except ContractValidationError as error:
                raise ModelProviderError(
                    "discovery_input_invalid",
                    "Scenario inputs do not satisfy the primary contract.",
                ) from error
            return CapabilityDraftSpec(
                operation_slug=primary.capability.id.rsplit(".", 1)[-1],
                capability_id=primary.capability.id,
                name=primary.capability.name,
                description=request.goal,
                inputs=primary.inputs,
                outputs=ObjectContract(required=(), properties=primary.outputs.properties),
                risk=Risk.READ_ONLY,
                observation_only=True,
            )
        if self.contract_planner is None:
            return None
        try:
            observation = session.observe()
            screenshot_png = session.capture_provider_frame()
            renew_control()
            draft = self.contract_planner(
                PlanningContext(
                    goal=request.goal,
                    inputs=request.inputs,
                    observation=observation,
                    screenshot_png=screenshot_png,
                    maximum_risk=effective_policy.maximum_risk,
                    requested_capability_id=request.existing_capability_id,
                    application_family=request.application_family,
                    entry_point=request.entry_point,
                    allowed_action_types=effective_policy.allowed_action_types,
                )
            )
            renew_control()
            if not isinstance(draft, CapabilityDraftSpec):
                raise ValueError("provider returned an invalid capability draft")
            if self.capability_id_resolver is not None:
                requested_id = request.existing_capability_id or draft.capability_id
                if requested_id is None:
                    capability_id = self.capability_id_resolver(
                        request.application_family, draft.operation_slug
                    )
                else:
                    requested_operation = requested_id.rsplit(".", 1)[-1]
                    if (
                        self.capability_id_resolver(request.application_family, requested_operation)
                        != requested_id
                    ):
                        raise ValueError("capability id must belong to the registered namespace")
                    capability_id = requested_id
                draft = draft.model_copy(update={"capability_id": capability_id})
            elif request.existing_capability_id is not None:
                if draft.capability_id not in {None, request.existing_capability_id}:
                    raise ValueError("provider capability id conflicts with the requested id")
                draft = draft.model_copy(update={"capability_id": request.existing_capability_id})
            validate_object(draft.inputs, request.inputs)
            return draft
        except ContractValidationError as error:
            raise ModelProviderError(
                "discovery_input_invalid",
                "Supplied inputs do not satisfy the planned capability contract.",
            ) from error
        except ValueError as error:
            raise ModelProviderError(
                "provider_contract_invalid",
                "The provider returned an invalid capability contract.",
            ) from error

    def _compile(
        self,
        request: DiscoveryRequest,
        recordings: list[RecordedDiscoveryStep],
        observation: NormalizedObservation,
        draft: CapabilityDraftSpec | None,
        effective_policy: EffectivePolicy,
        rendered_surface: bool,
        base_variant: str,
        surface_contract: str,
        required_landmarks: tuple[Landmark, ...],
        forbidden_landmarks: tuple[Landmark, ...],
    ) -> CapabilityArtifact:
        arguments = {
            "run_id": request.run_id,
            "goal": request.goal,
            "application_family": request.application_family,
            "tenant": request.tenant,
            "entry_point": request.entry_point,
            "steps": tuple(recordings),
            "final_observation": observation,
            "provider_name": self.model_provider.provider_name,
            "model_name": self.model_provider.model_name,
            "evidence_manifest": self.recorder.evidence_manifest_key,
            "allowed_route_patterns": effective_policy.allowed_route_patterns,
            "rendered_surface": rendered_surface,
            "base_variant": base_variant,
            "surface_contract": surface_contract,
            "maximum_risk": effective_policy.maximum_risk,
            "allowed_action_types": effective_policy.allowed_action_types,
            "required_landmarks": required_landmarks,
            "forbidden_landmarks": forbidden_landmarks,
        }
        compile_with_spec = getattr(self.artifact_compiler, "compile_with_spec", None)
        if draft is not None and callable(compile_with_spec):
            compiler = cast(Callable[..., CapabilityArtifact], compile_with_spec)
            return compiler(**arguments, draft=draft)
        return self.artifact_compiler.compile(
            run_id=request.run_id,
            goal=request.goal,
            application_family=request.application_family,
            tenant=request.tenant,
            entry_point=request.entry_point,
            steps=tuple(recordings),
            final_observation=observation,
            provider_name=self.model_provider.provider_name,
            model_name=self.model_provider.model_name,
            evidence_manifest=self.recorder.evidence_manifest_key,
        )

    def _act(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        renew_control: Callable[[], None],
        proposal: ActProposal,
        before: NormalizedObservation,
        outputs: dict[str, Any],
        effective_policy: EffectivePolicy,
        output_contract: ObjectContract,
        input_contract: ObjectContract | None,
    ) -> tuple[RecordedDiscoveryStep, str] | FailureResult:
        if isinstance(proposal.action, AssertAction | WaitForAction):
            validate_condition_bindings(proposal.action.condition, outputs, request.inputs)
        value_source = (
            proposal.action.value
            if isinstance(proposal.action, TypeAction)
            else proposal.action.option
            if isinstance(proposal.action, SelectAction)
            else None
        )
        if isinstance(value_source, LiteralValue) and self._contains_input_literal(
            request.inputs, str(value_source.value)
        ):
            return self._failure(
                request,
                "literal_customer_value",
                "Discovery cannot publish a customer value embedded in an action.",
            )
        if isinstance(value_source, InputValue):
            try:
                resolve_input(request.inputs, value_source.path)
            except ContractValidationError as error:
                raise SurfaceError(
                    "input_binding_missing",
                    "The proposed input binding is unavailable.",
                    recoverable=True,
                    effect_absent=True,
                ) from error
        if isinstance(proposal.action, ExtractAction):
            output_name = proposal.action.output
            if output_name not in output_contract.properties:
                raise SurfaceError(
                    "output_not_declared",
                    "Extraction output is not declared by the capability contract.",
                    recoverable=True,
                    effect_absent=True,
                )
        if proposal.target and target_contains_invocation_literal(proposal.target, request.inputs):
            raise SurfaceError(
                "literal_input_target",
                "Use an input_text symbolic binding for invocation-dependent record identity.",
                recoverable=True,
                effect_absent=True,
            )
        try:
            bound_target = (
                bind_target_inputs(
                    proposal.target,
                    request.inputs,
                    input_contract,
                    effective_policy.forbidden_field_classes,
                )
                if proposal.target
                else None
            )
        except ContractValidationError as error:
            raise SurfaceError(
                error.code,
                "Target input binding is unavailable or forbidden.",
                recoverable=error.code != "target_input_forbidden",
                effect_absent=True,
            ) from error
        target = session.resolve(bound_target, 10_000) if bound_target else None
        stable_target = (
            proposal.target
            if proposal.target and target_input_paths(proposal.target)
            else session.capture_locator(target)
            if target
            else None
        )
        decision = self.policy_evaluator.evaluate(
            effective_policy,
            ActionContext(
                principal_type=PrincipalType.AUTOMATION,
                principal_id="runtime",
                run_mode=RunMode.DISCOVERY,
                application_family=request.application_family,
                tenant=request.tenant,
                origin=session.origin,
                route=before.route,
                action_type=proposal.action.kind,
                target_description=(
                    proposal.target.description if proposal.target else proposal.expected_effect
                ),
                declared_risk=proposal.declared_risk,
                registered_target_risk=(target.registered_risk if target else None),
                field_classification=(
                    binding_classification(
                        input_contract, value_source.path, effective_policy.forbidden_field_classes
                    )
                    if input_contract is not None and isinstance(value_source, InputValue)
                    else None
                ),
                control_owner=AUTOMATION_OWNER.value,
            ),
        )
        self.recorder.record(
            "policy_evaluated",
            request.run_id,
            details={"decision": decision.decision.value, "reason": decision.reason_code},
        )
        if decision.decision is Decision.DENY:
            return self._failure(request, "policy_blocked", decision.explanation)
        if decision.decision is Decision.REQUIRE_HUMAN_APPROVAL:
            if decision.reason_code == "sensitive_action_requires_approval":
                raise SurfaceError(
                    "risk_classification_unresolved",
                    "The proposal must identify a verified inverse or escalate before mutation.",
                    recoverable=True,
                    effect_absent=True,
                )
            raise DiscoveryBlockedError(decision.reason_code)
        renew_control()
        self.recorder.record("action_intent", request.run_id)
        if isinstance(proposal.action, ExtractAction):
            if target is None:
                return self._failure(
                    request, "target_absent", "Extraction requires a resolved target."
                )
            observed_value = session.extract(target)
            value = self._transform(observed_value, proposal.action.transform)
            if extraction_locator_contains_value(
                stable_target, observed_value
            ) or extraction_locator_contains_value(stable_target, value):
                raise SurfaceError(
                    "extraction_locator_value_bound",
                    "The extraction locator depends on the observed output value.",
                    recoverable=True,
                    effect_absent=True,
                )
            outputs[proposal.action.output] = value
        else:
            receipt = session.execute(proposal.action, target, request.inputs)
            if receipt.status is ActionStatus.FAILED:
                return self._failure(
                    request,
                    receipt.error_code or "action_failed",
                    "The discovery action did not complete.",
                )
        renew_control()
        verified_postconditions = []
        if isinstance(proposal.action, WaitForAction | AssertAction):
            if not session.wait_until(proposal.action.condition, outputs, request.inputs, 10_000):
                return self._failure(
                    request,
                    "action_condition_not_verified",
                    "The action's condition was not observed.",
                )
            verified_postconditions.append(proposal.action.condition)
            renew_control()
        if proposal.expected_condition is not None:
            if not session.wait_until(proposal.expected_condition, outputs, request.inputs, 10_000):
                return self._failure(
                    request,
                    "expected_condition_not_verified",
                    "The action's expected condition was not observed after execution.",
                )
            if proposal.expected_condition not in verified_postconditions:
                verified_postconditions.append(proposal.expected_condition)
            renew_control()
        if isinstance(proposal.action, ExtractAction):
            output = proposal.action.output
            schema = output_contract.properties[output]
            try:
                validate_object(
                    ObjectContract(required=(output,), properties={output: schema}),
                    {output: outputs[output]},
                )
            except (ContractValidationError, KeyError):
                self.recorder.record(
                    "output_validation_failed",
                    request.run_id,
                    details={"output": output},
                )
                return self._failure(request, "output_invalid", "The extracted output is invalid.")
            verified_postconditions.append(OutputValidCondition(kind="output_valid", output=output))
        after = session.observe()
        self.recorder.record("action_result", request.run_id)
        recorded = RecordedDiscoveryStep(
            action=proposal.action,
            target=stable_target,
            observation_before=before,
            observation_after=after,
            expected_effect=proposal.expected_effect,
            rationale=proposal.rationale,
            risk=decision.effective_risk,
            verified_postconditions=tuple(verified_postconditions),
        )
        history = "Completed action: " + self._proposal_fingerprint(proposal)
        if isinstance(proposal.action, AssertAction | WaitForAction):
            history += " Condition verified and retained in the recorded trace."
        return recorded, history

    def _failure(
        self,
        request: DiscoveryRequest,
        code: str,
        message: str,
        *,
        privacy_rejection: ArtifactPrivacyDiagnostic | None = None,
    ) -> FailureResult:
        self.recorder.record("discovery_failed", request.run_id, details={"code": code})
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code=code,
            message=message,
            recoverable=False,
            evidence_manifest=self.recorder.evidence_manifest_key,
            privacy_rejection=privacy_rejection,
        )

    @staticmethod
    def _operation_fingerprint(proposal: ActProposal) -> str:
        """Compare executable intent, not changing confidence or explanatory prose."""
        target = proposal.target.model_dump(mode="json") if proposal.target is not None else None
        if target is not None:
            target.pop("description", None)
        return json.dumps(
            {"action": proposal.action.model_dump(mode="json"), "target": target},
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _proposal_fingerprint(proposal: ActProposal) -> str:
        return json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _proposal_summary(
        proposal: DiscoveryProposal,
    ) -> dict[str, object]:
        if isinstance(proposal, RecordedActionProposal):
            return {"proposal_kind": proposal.kind, "source_step_id": proposal.step_id}
        if not isinstance(proposal, ActProposal):
            return {"proposal_kind": proposal.kind}
        target = proposal.target
        summary: dict[str, object] = {
            "proposal_kind": proposal.kind,
            "action_type": proposal.action.kind,
            "declared_risk": proposal.declared_risk.value,
            "target_present": target is not None,
            "frame_depth": len(target.scope.frame_path) if target is not None else 0,
            "locator_strategies": (
                [candidate.strategy for candidate in target.visual_candidates]
                + [candidate.strategy.value for candidate in target.candidates]
                if target is not None
                else []
            ),
        }
        if isinstance(proposal.action, TypeAction) and isinstance(
            proposal.action.value, InputValue
        ):
            summary["input_binding"] = proposal.action.value.path
        if isinstance(proposal.action, ExtractAction):
            summary["output_binding"] = proposal.action.output
            summary["transform"] = proposal.action.transform
        if isinstance(proposal.action, AssertAction | WaitForAction):
            # Retain the discriminator, never the expected text or customer value.
            summary["condition_kind"] = proposal.action.condition.kind
        return summary

    @staticmethod
    def _contains_input_literal(inputs: object, literal: str) -> bool:
        if isinstance(inputs, dict):
            return any(
                DiscoveryEngine._contains_input_literal(value, literal) for value in inputs.values()
            )
        if isinstance(inputs, list):
            return any(DiscoveryEngine._contains_input_literal(value, literal) for value in inputs)
        return str(inputs) == literal

    @staticmethod
    def _transform(value: str, transform: str) -> str:
        return transform_extracted_text(value, transform)
