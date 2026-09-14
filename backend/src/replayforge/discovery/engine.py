"""Bounded observe-decide-act orchestration for capability discovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

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
from replayforge.capabilities.values import (
    ContractValidationError,
    binding_classification,
    resolve_input,
    validate_object,
)
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
    CapabilityDraftSpec,
    CompleteProposal,
    DiscoveryResult,
    DiscoverySuccess,
    EscalateProposal,
    PlanningContext,
    ProviderContext,
    RecordedDiscoveryStep,
)
from replayforge.discovery.ports import ArtifactCompiler, ModelProvider, ModelProviderError
from replayforge.discovery.privacy import validate_artifact_privacy
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.interventions.leases import ControlLeaseService
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
from replayforge.policy.types import Decision
from replayforge.runs.ports import InterventionRouter, RunRecorder
from replayforge.runs.results import FailureResult, InterventionRequiredResult
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
class DiscoveryEngine:
    surface_driver: SurfaceDriver
    model_provider: ModelProvider
    artifact_compiler: ArtifactCompiler
    policy_evaluator: PolicyEvaluator
    effective_policy: EffectivePolicy | None
    lease_service: ControlLeaseService
    recorder: RunRecorder
    intervention_router: InterventionRouter
    clock: Clock
    policy_resolver: Callable[[DiscoveryRequest], EffectivePolicy] | None = None
    contract_planner: Callable[[PlanningContext], CapabilityDraftSpec] | None = None
    capability_id_resolver: Callable[[str, str], str] | None = None
    privacy_redactor: StructuredRedactor = field(default_factory=StructuredRedactor)

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        session: SurfaceSession | None = None
        preserve_session = False
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
            self.recorder.record("discovery_started", request.run_id)
            draft = self._plan_contract(request, session, effective_policy)
            output_contract = (
                draft.outputs if draft is not None else self.artifact_compiler.output_contract
            )
            recordings: list[RecordedDiscoveryStep] = []
            history: list[str] = []
            outputs: dict[str, Any] = {}
            previous_fingerprint: str | None = None
            repeated_state = 0
            repeated_action = 0
            previous_action: str | None = None

            for _step_number in range(1, request.max_steps + 1):
                if self.clock.now() - started_at >= request.timeout:
                    return self._failure(request, "discovery_timeout", "Time budget exhausted.")
                lease = self.lease_service.heartbeat(
                    session.session_id, lease.version, AUTOMATION_OWNER
                )
                observation = session.observe()
                self.recorder.record("observation_captured", request.run_id)
                previous_was_extraction = bool(recordings) and isinstance(
                    recordings[-1].action, ExtractAction
                )
                if observation.fingerprint == previous_fingerprint and not previous_was_extraction:
                    repeated_state += 1
                else:
                    repeated_state = 0
                previous_fingerprint = observation.fingerprint
                if repeated_state >= request.max_repeated_state:
                    result = self._intervene(
                        request,
                        session,
                        lease.version,
                        "repeated_observation",
                        None,
                        observation,
                    )
                    preserve_session = True
                    return result

                proposal = self.model_provider.decide(
                    ProviderContext(
                        goal=request.goal,
                        inputs=request.inputs,
                        observation=observation,
                        screenshot_png=session.capture_provider_frame(),
                        action_history=tuple(history),
                        allowed_action_types=effective_policy.allowed_action_types,
                        output_contract=output_contract,
                        captured_output_names=tuple(
                            name for name in output_contract.required if name in outputs
                        ),
                        maximum_risk=effective_policy.maximum_risk,
                    )
                )
                self.recorder.record(
                    "model_proposal_received",
                    request.run_id,
                    details=self._proposal_summary(proposal),
                )
                if isinstance(proposal, EscalateProposal):
                    result = self._intervene(
                        request,
                        session,
                        lease.version,
                        proposal.reason_code,
                        None,
                        observation,
                    )
                    preserve_session = True
                    return result
                if isinstance(proposal, CompleteProposal):
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
                        validate_artifact_privacy(artifact, request.inputs, self.privacy_redactor)
                    except ValueError:
                        return self._failure(
                            request,
                            "artifact_compilation_failed",
                            "The observed trace could not be compiled into a safe capability.",
                        )
                    if not session.wait_until(
                        artifact.checkpoint.condition,
                        outputs,
                        request.inputs,
                        10_000,
                    ):
                        return self._failure(
                            request,
                            "completion_not_verified",
                            "Model completion lacked deterministic checkpoint evidence.",
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
                    )

                action_fingerprint = self._proposal_fingerprint(proposal)
                repeated_action = (
                    repeated_action + 1 if action_fingerprint == previous_action else 0
                )
                previous_action = action_fingerprint
                if repeated_action >= request.max_repeated_action:
                    result = self._intervene(
                        request,
                        session,
                        lease.version,
                        "repeated_action",
                        None,
                        observation,
                    )
                    preserve_session = True
                    return result
                if proposal.confidence < request.minimum_confidence:
                    result = self._intervene(
                        request,
                        session,
                        lease.version,
                        "low_model_confidence",
                        None,
                        observation,
                    )
                    preserve_session = True
                    return result

                try:
                    act_result = self._act(
                        request,
                        session,
                        lease.version,
                        proposal,
                        observation,
                        outputs,
                        effective_policy,
                        output_contract,
                        draft.inputs if draft is not None else None,
                    )
                except SurfaceError as error:
                    if not error.recoverable or not error.effect_absent:
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
                            "Previous proposal was not executed (target_ambiguous); use "
                            "ocr_relative with a unique nearby anchor, the complete target_text, "
                            "and its observed relation."
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
                if isinstance(act_result, InterventionRequiredResult):
                    preserve_session = True
                    return act_result
                if isinstance(act_result, FailureResult):
                    return act_result
                recording, history_item = act_result
                recordings.append(recording)
                history.append(history_item)

            return self._failure(request, "max_steps_exceeded", "Discovery step budget exhausted.")
        except ModelProviderError as error:
            return self._failure(request, error.code, error.safe_message)
        except SurfaceError as error:
            return self._failure(request, error.code, error.safe_message)
        finally:
            if session is not None and not preserve_session:
                session.close()

    def _plan_contract(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        effective_policy: EffectivePolicy,
    ) -> CapabilityDraftSpec | None:
        if self.contract_planner is None:
            return None
        try:
            draft = self.contract_planner(
                PlanningContext(
                    goal=request.goal,
                    inputs=request.inputs,
                    observation=session.observe(),
                    screenshot_png=session.capture_provider_frame(),
                    maximum_risk=effective_policy.maximum_risk,
                    requested_capability_id=request.existing_capability_id,
                    application_family=request.application_family,
                    entry_point=request.entry_point,
                    allowed_action_types=effective_policy.allowed_action_types,
                )
            )
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
        lease_version: int,
        proposal: ActProposal,
        before: NormalizedObservation,
        outputs: dict[str, Any],
        effective_policy: EffectivePolicy,
        output_contract: ObjectContract,
        input_contract: ObjectContract | None,
    ) -> tuple[RecordedDiscoveryStep, str] | FailureResult | InterventionRequiredResult:
        value_source = (
            proposal.action.value
            if isinstance(proposal.action, TypeAction)
            else proposal.action.option
            if isinstance(proposal.action, SelectAction)
            else None
        )
        if isinstance(value_source, LiteralValue) and (
            self._contains_input_literal(request.inputs, str(value_source.value))
            or (isinstance(value_source.value, str) and value_source.value.isdigit())
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
            if output_name not in output_contract.required:
                raise SurfaceError(
                    "output_not_declared",
                    "Extraction output is not declared by the capability contract.",
                    recoverable=True,
                    effect_absent=True,
                )
            if output_name in outputs:
                raise SurfaceError(
                    "output_already_captured",
                    "Extraction output was already captured in this run.",
                    recoverable=True,
                    effect_absent=True,
                )
        target = session.resolve(proposal.target, 10_000) if proposal.target else None
        stable_target = session.capture_locator(target) if target else None
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
            return self._intervene(
                request,
                session,
                lease_version,
                decision.reason_code,
                None,
                before,
            )
        self.recorder.record("action_intent", request.run_id)
        if isinstance(proposal.action, ExtractAction):
            if target is None:
                return self._failure(
                    request, "target_absent", "Extraction requires a resolved target."
                )
            outputs[proposal.action.output] = self._transform(
                session.extract(target), proposal.action.transform
            )
        else:
            receipt = session.execute(proposal.action, target, request.inputs)
            if receipt.status is ActionStatus.FAILED:
                return self._failure(
                    request,
                    receipt.error_code or "action_failed",
                    "The discovery action did not complete.",
                )
        verified_postconditions = []
        if isinstance(proposal.action, WaitForAction | AssertAction):
            if not session.wait_until(proposal.action.condition, outputs, request.inputs, 10_000):
                return self._failure(
                    request,
                    "action_condition_not_verified",
                    "The action's condition was not observed.",
                )
            verified_postconditions.append(proposal.action.condition)
        if proposal.expected_condition is not None:
            if not session.wait_until(proposal.expected_condition, outputs, request.inputs, 10_000):
                return self._failure(
                    request,
                    "expected_condition_not_verified",
                    "The action's expected condition was not observed after execution.",
                )
            if proposal.expected_condition not in verified_postconditions:
                verified_postconditions.append(proposal.expected_condition)
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
        return recorded, self._proposal_fingerprint(proposal)

    def _intervene(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        lease_version: int,
        code: str,
        step_id: str | None,
        observation: NormalizedObservation,
    ) -> InterventionRequiredResult:
        intervention_id = new_id(EntityKind.INTERVENTION)
        routed_id = self.intervention_router.open(
            intervention_id=intervention_id,
            run_id=request.run_id,
            session_id=session.session_id,
            expected_lease_version=lease_version,
            code=code,
            step_id=step_id,
            observation=observation,
            context=InterventionContext(
                run_mode=InterventionRunMode.DISCOVERY,
                application_family=request.application_family,
                tenant=request.tenant,
                task_summary="Discovery run requires operator intervention.",
                step_id=step_id,
                surface_route=observation.route,
            ),
        )
        if routed_id != intervention_id:
            raise RuntimeError("intervention router must preserve the reserved identity")
        return InterventionRequiredResult(
            status="intervention_required",
            run_id=request.run_id,
            intervention_id=intervention_id,
            code=code,
            step_id=step_id,
            session_live=True,
            control_owner="automation_paused",
        )

    def _failure(self, request: DiscoveryRequest, code: str, message: str) -> FailureResult:
        self.recorder.record("discovery_failed", request.run_id, details={"code": code})
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code=code,
            message=message,
            recoverable=False,
            evidence_manifest=self.recorder.evidence_manifest_key,
        )

    @staticmethod
    def _proposal_fingerprint(proposal: ActProposal) -> str:
        return json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _proposal_summary(
        proposal: ActProposal | CompleteProposal | EscalateProposal,
    ) -> dict[str, object]:
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
        if transform == "lowercase":
            return value.strip().lower()
        if transform == "decimal":
            return value.strip().removeprefix("$").replace(",", "")
        if transform in {"trim", "date-time"}:
            return value.strip()
        return value
