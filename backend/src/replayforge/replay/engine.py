"""Deterministic artifact executor with policy, lease, and checkpoint enforcement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any

from replayforge.capabilities.models import (
    BusinessOutcome,
    CapabilityArtifact,
    ExtractAction,
    InputValue,
    LiteralValue,
    Step,
)
from replayforge.capabilities.values import ContractValidationError, validate_object
from replayforge.interventions.leases import ControlLeaseService
from replayforge.interventions.models import AUTOMATION_OWNER
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import (
    ActionContext,
    EffectivePolicy,
    PrincipalType,
    RunMode,
)
from replayforge.policy.types import Decision
from replayforge.runs.ports import InterventionRouter, RunRecorder
from replayforge.runs.results import (
    BusinessOutcomeResult,
    CapabilityReference,
    FailureResult,
    InterventionRequiredResult,
    RunResult,
    SuccessResult,
    VerifiedCheckpoint,
)
from replayforge.surfaces.models import ActionStatus, NormalizedObservation, SurfaceError
from replayforge.surfaces.ports import SurfaceDriver, SurfaceSession


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    run_id: str
    artifact: CapabilityArtifact
    tenant: str
    inputs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayEngine:
    surface_driver: SurfaceDriver
    policy_evaluator: PolicyEvaluator
    effective_policy: EffectivePolicy
    lease_service: ControlLeaseService
    recorder: RunRecorder
    intervention_router: InterventionRouter
    sleeper: Callable[[float], None] = sleep

    def execute(self, request: ReplayRequest) -> RunResult:
        try:
            inputs = validate_object(request.artifact.inputs, request.inputs)
        except ContractValidationError as error:
            return self._failure(request, "invalid_input", str(error), recoverable=False)
        if request.tenant not in request.artifact.compatibility.supported_variants:
            return self._failure(
                request, "incompatible_tenant", "Tenant is not supported by this artifact.", False
            )

        session: SurfaceSession | None = None
        preserve_session = False
        try:
            session = self.surface_driver.open(
                request.artifact.capability.application_family,
                request.tenant,
                request.artifact.compatibility.entry_point,
            )
            lease = self.lease_service.create_for_automation(session.session_id)
            self.recorder.record("replay_started", request.run_id)
            outputs: dict[str, Any] = {}
            for condition in request.artifact.preconditions:
                if not session.evaluate(condition, outputs, inputs):
                    return self._failure(
                        request,
                        "precondition_mismatch",
                        "A capability precondition was not satisfied.",
                        False,
                    )

            for step in request.artifact.steps:
                result = self._execute_step(request, session, step, inputs, outputs, lease.version)
                if result is not None:
                    preserve_session = isinstance(result, InterventionRequiredResult)
                    return result

            if not session.wait_until(
                request.artifact.checkpoint.condition, outputs, inputs, 10_000
            ):
                return self._failure(
                    request,
                    "checkpoint_mismatch",
                    "The final success checkpoint was not satisfied.",
                    False,
                )
            try:
                validated_outputs = validate_object(request.artifact.outputs, outputs)
            except ContractValidationError as error:
                return self._failure(request, "output_validation_failed", str(error), False)
            self.recorder.record("checkpoint_verified", request.run_id)
            return SuccessResult(
                status="success",
                run_id=request.run_id,
                capability=CapabilityReference(
                    id=request.artifact.capability.id,
                    version=request.artifact.capability.version,
                ),
                outputs=validated_outputs,
                checkpoint=VerifiedCheckpoint(id=request.artifact.checkpoint.id, verified=True),
                evidence_manifest=self.recorder.evidence_manifest_key,
            )
        except SurfaceError as error:
            result = self._surface_failure(request, session, None, error)
            preserve_session = isinstance(result, InterventionRequiredResult)
            return result
        finally:
            if session is not None and not preserve_session:
                session.close()

    def _execute_step(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step: Step,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        lease_version: int,
        attempt: int = 1,
    ) -> RunResult | None:
        try:
            self.lease_service.assert_can_act(session.session_id, lease_version, AUTOMATION_OWNER)
            for condition in step.preconditions:
                if not session.evaluate(condition, outputs, inputs):
                    return self._failure(
                        request,
                        "step_precondition_mismatch",
                        "A step precondition was not satisfied.",
                        False,
                        step.id,
                    )
            observation = session.observe()
            target = session.resolve(step.target, step.timeout_ms) if step.target else None
            decision = self.policy_evaluator.evaluate(
                self.effective_policy,
                ActionContext(
                    principal_type=PrincipalType.AUTOMATION,
                    principal_id="runtime",
                    run_mode=RunMode.REPLAY,
                    application_family=request.artifact.capability.application_family,
                    tenant=request.tenant,
                    origin=session.origin,
                    route=observation.route,
                    action_type=step.action.kind,
                    target_description=step.target.description if step.target else step.name,
                    declared_risk=step.risk,
                    registered_target_risk=(
                        target.registered_risk if target is not None else step.risk
                    ),
                    control_owner=AUTOMATION_OWNER.value,
                ),
            )
            self.recorder.record(
                "policy_evaluated",
                request.run_id,
                step_id=step.id,
                details={"decision": decision.decision.value, "reason": decision.reason_code},
            )
            if decision.decision is Decision.DENY:
                return self._failure(
                    request, "policy_blocked", decision.explanation, False, step.id
                )
            if decision.decision is Decision.REQUIRE_HUMAN_APPROVAL:
                return self._intervene(
                    request, session, step.id, decision.reason_code, observation, lease_version
                )

            self.recorder.record("action_intent", request.run_id, step_id=step.id)
            if isinstance(step.action, ExtractAction):
                if target is None:
                    raise SurfaceError("target_absent", "Extraction target was not resolved.")
                outputs[step.action.output] = self._transform(
                    session.extract(target), step.action.transform
                )
            else:
                receipt = session.execute(step.action, target, inputs)
                if receipt.status is ActionStatus.FAILED:
                    raise SurfaceError(
                        receipt.error_code or "action_failed",
                        "The surface adapter could not complete the action.",
                    )
            self.recorder.record("action_result", request.run_id, step_id=step.id)

            outcome = self._detect_outcome(request.artifact, step, session, outputs, inputs)
            if outcome is not None:
                return self._business_outcome(request, outcome, inputs)
            for condition in step.postconditions:
                if not session.wait_until(condition, outputs, inputs, step.timeout_ms):
                    return self._failure(
                        request,
                        "postcondition_mismatch",
                        "The action completed but its declared effect was not observed.",
                        False,
                        step.id,
                    )
            return None
        except SurfaceError as error:
            can_retry = (
                error.recoverable
                and (error.effect_absent or not step.retry.require_effect_absent)
                and error.code in step.retry.retry_on
                and attempt < step.retry.max_attempts
            )
            if can_retry:
                backoff_index = attempt - 1
                backoff_ms = (
                    step.retry.backoff_ms[backoff_index]
                    if backoff_index < len(step.retry.backoff_ms)
                    else 0
                )
                self.recorder.record(
                    "step_retry_scheduled",
                    request.run_id,
                    step_id=step.id,
                    details={"attempt": attempt + 1, "reason": error.code},
                )
                self.sleeper(backoff_ms / 1000)
                return self._execute_step(
                    request,
                    session,
                    step,
                    inputs,
                    outputs,
                    lease_version,
                    attempt + 1,
                )
            return self._surface_failure(request, session, step.id, error, lease_version)

    @staticmethod
    def _detect_outcome(
        artifact: CapabilityArtifact,
        step: Step,
        session: SurfaceSession,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
    ) -> BusinessOutcome | None:
        indexed = {outcome.code: outcome for outcome in artifact.outcomes}
        for code in step.outcome_refs:
            outcome = indexed[code]
            if session.evaluate(outcome.detect, outputs, inputs):
                return outcome
        return None

    def _business_outcome(
        self,
        request: ReplayRequest,
        outcome: BusinessOutcome,
        inputs: dict[str, Any],
    ) -> BusinessOutcomeResult:
        details: dict[str, Any] = {}
        for name, source in outcome.result.details.items():
            if isinstance(source, InputValue):
                raw = str(inputs[source.path])
                details[name] = f"***{raw[-4:]}" if raw else "[REDACTED]"
            elif isinstance(source, LiteralValue):
                details[name] = source.value
        self.recorder.record("business_outcome", request.run_id)
        return BusinessOutcomeResult(
            status="business_outcome",
            run_id=request.run_id,
            code=outcome.code,
            details=details,
            evidence_manifest=self.recorder.evidence_manifest_key,
        )

    def _surface_failure(
        self,
        request: ReplayRequest,
        session: SurfaceSession | None,
        step_id: str | None,
        error: SurfaceError,
        lease_version: int | None = None,
    ) -> RunResult:
        if error.intervention_recommended and session is not None and lease_version is not None:
            return self._intervene(
                request,
                session,
                step_id,
                error.code,
                session.observe(),
                lease_version,
            )
        return self._failure(
            request,
            error.code,
            error.safe_message,
            error.recoverable,
            step_id,
            error.expected,
            error.observed,
        )

    def _intervene(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step_id: str | None,
        code: str,
        observation: NormalizedObservation,
        lease_version: int,
    ) -> InterventionRequiredResult:
        from replayforge.shared.ids import EntityKind, new_id

        intervention_id = new_id(EntityKind.INTERVENTION)
        self.lease_service.pause(session.session_id, lease_version, intervention_id)
        routed_id = self.intervention_router.create(
            intervention_id=intervention_id,
            run_id=request.run_id,
            session_id=session.session_id,
            code=code,
            step_id=step_id,
            observation=observation,
        )
        if routed_id != intervention_id:
            raise RuntimeError("intervention router must preserve the reserved identity")
        self.recorder.record("intervention_required", request.run_id, step_id=step_id)
        return InterventionRequiredResult(
            status="intervention_required",
            run_id=request.run_id,
            intervention_id=intervention_id,
            code=code,
            step_id=step_id,
            session_live=True,
            control_owner="automation_paused",
        )

    def _failure(
        self,
        request: ReplayRequest,
        code: str,
        message: str,
        recoverable: bool,
        step_id: str | None = None,
        expected: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
    ) -> FailureResult:
        self.recorder.record(
            "replay_failed", request.run_id, step_id=step_id, details={"code": code}
        )
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code=code,
            message=message,
            recoverable=recoverable,
            step_id=step_id,
            expected=expected,
            observed=observed,
            evidence_manifest=self.recorder.evidence_manifest_key,
        )

    @staticmethod
    def _transform(value: str, transform: str) -> str:
        if transform in {"trim", "decimal", "date-time"}:
            return value.strip().removeprefix("$").replace(",", "")
        return value
