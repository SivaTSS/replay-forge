"""Deterministic artifact executor with policy, lease, and checkpoint enforcement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any, cast

from pydantic import JsonValue

from replayforge.capabilities.models import (
    ApplicationFailure,
    BusinessOutcome,
    CapabilityArtifact,
    ExtractAction,
    InputValue,
    LiteralValue,
    Recovery,
    Step,
)
from replayforge.capabilities.values import ContractValidationError, validate_object
from replayforge.evidence.models import RetentionClass, SanitizedEvidence
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


class ResumeValidationError(RuntimeError):
    """The retained surface does not satisfy the interrupted step contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class ReplayContinuation:
    intervention_id: str
    request: ReplayRequest
    session: SurfaceSession
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    recovery_uses: dict[str, int]
    interrupted_step_index: int
    initial_fingerprint: str


type ContinuationSink = Callable[[ReplayContinuation], None]


@dataclass(frozen=True, slots=True)
class RecoveryResume:
    step_id: str


@dataclass(frozen=True, slots=True)
class ReplayEngine:
    surface_driver: SurfaceDriver
    policy_evaluator: PolicyEvaluator
    effective_policy: EffectivePolicy
    lease_service: ControlLeaseService
    recorder: RunRecorder
    intervention_router: InterventionRouter
    sleeper: Callable[[float], None] = sleep
    continuation_sink: ContinuationSink | None = None

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
            recovery_uses: dict[str, int] = {}
            for condition in request.artifact.preconditions:
                if not session.evaluate(condition, outputs, inputs):
                    return self._failure(
                        request,
                        "precondition_mismatch",
                        "A capability precondition was not satisfied.",
                        False,
                        session=session,
                    )

            step_indexes = {
                step.id: step_index for step_index, step in enumerate(request.artifact.steps)
            }
            step_index = 0
            while step_index < len(request.artifact.steps):
                step = request.artifact.steps[step_index]
                result = self._execute_step(
                    request,
                    session,
                    step,
                    inputs,
                    outputs,
                    recovery_uses,
                    lease.version,
                )
                if isinstance(result, RecoveryResume):
                    lease = self.lease_service.heartbeat(
                        session.session_id, lease.version, AUTOMATION_OWNER
                    )
                    step_index = step_indexes[result.step_id]
                    continue
                if result is not None:
                    preserve_session = isinstance(result, InterventionRequiredResult)
                    if isinstance(result, InterventionRequiredResult):
                        self._retain_continuation(
                            result,
                            request,
                            session,
                            inputs,
                            outputs,
                            recovery_uses,
                            step_index,
                        )
                    return result
                lease = self.lease_service.heartbeat(
                    session.session_id, lease.version, AUTOMATION_OWNER
                )
                step_index += 1

            return self._complete(request, session, inputs, outputs)
        except SurfaceError as error:
            result = self._surface_failure(request, session, None, error)
            preserve_session = isinstance(result, InterventionRequiredResult)
            return result
        finally:
            if session is not None and not preserve_session:
                session.close()

    def validate_resume(self, continuation: ReplayContinuation) -> BusinessOutcome | None:
        self._assert_continuation(continuation)
        step = continuation.request.artifact.steps[continuation.interrupted_step_index]
        session = continuation.session
        observation = session.observe()
        self.recorder.record(
            "resume_revalidation_started",
            continuation.request.run_id,
            step_id=step.id,
        )
        if not self.policy_evaluator.location_allowed(
            self.effective_policy, session.origin, observation.route
        ):
            raise ResumeValidationError(
                "resume_location_not_allowed",
                "The retained session is outside the capability's allowed location.",
            )
        outcome = self._detect_outcome(
            continuation.request.artifact,
            step,
            session,
            continuation.outputs,
            continuation.inputs,
        )
        if outcome is not None:
            self._attach_handoff_frame(continuation.request.run_id, session, "handoff-after")
            self.recorder.record(
                "resume_checkpoint_verified",
                continuation.request.run_id,
                step_id=step.id,
                details={"disposition": "business_outcome"},
            )
            return outcome
        if not step.postconditions:
            raise ResumeValidationError(
                "resume_checkpoint_missing",
                "The interrupted step has no declared postcondition for safe resumption.",
            )
        if not all(
            session.wait_until(condition, continuation.outputs, continuation.inputs, 2_000)
            for condition in step.postconditions
        ):
            raise ResumeValidationError(
                "resume_checkpoint_mismatch",
                "The human-modified state does not satisfy the interrupted step postcondition.",
            )
        if session.observe().fingerprint == continuation.initial_fingerprint:
            raise ResumeValidationError(
                "resume_state_unchanged",
                "The retained session has not changed since automation paused.",
            )
        self._attach_handoff_frame(continuation.request.run_id, session, "handoff-after")
        self.recorder.record(
            "resume_checkpoint_verified",
            continuation.request.run_id,
            step_id=step.id,
            details={"disposition": "continue"},
        )
        return None

    def resume(
        self,
        continuation: ReplayContinuation,
        automation_lease_version: int,
        outcome: BusinessOutcome | None = None,
    ) -> RunResult:
        self._assert_continuation(continuation)
        request = continuation.request
        session = continuation.session
        if outcome is not None:
            session.close()
            return self._business_outcome(request, outcome, continuation.inputs)
        preserve_session = False
        try:
            step_indexes = {
                step.id: step_index for step_index, step in enumerate(request.artifact.steps)
            }
            step_index = continuation.interrupted_step_index + 1
            while step_index < len(request.artifact.steps):
                step = request.artifact.steps[step_index]
                result = self._execute_step(
                    request,
                    session,
                    step,
                    continuation.inputs,
                    continuation.outputs,
                    continuation.recovery_uses,
                    automation_lease_version,
                )
                if isinstance(result, RecoveryResume):
                    refreshed = self.lease_service.heartbeat(
                        session.session_id, automation_lease_version, AUTOMATION_OWNER
                    )
                    automation_lease_version = refreshed.version
                    step_index = step_indexes[result.step_id]
                    continue
                if result is not None:
                    preserve_session = isinstance(result, InterventionRequiredResult)
                    if isinstance(result, InterventionRequiredResult):
                        self._retain_continuation(
                            result,
                            request,
                            session,
                            continuation.inputs,
                            continuation.outputs,
                            continuation.recovery_uses,
                            step_index,
                        )
                    return result
                refreshed = self.lease_service.heartbeat(
                    session.session_id, automation_lease_version, AUTOMATION_OWNER
                )
                automation_lease_version = refreshed.version
                step_index += 1
            return self._complete(request, session, continuation.inputs, continuation.outputs)
        except SurfaceError as error:
            result = self._surface_failure(request, session, None, error, automation_lease_version)
            preserve_session = isinstance(result, InterventionRequiredResult)
            return result
        finally:
            if not preserve_session:
                session.close()

    def _execute_step(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step: Step,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        recovery_uses: dict[str, int],
        lease_version: int,
        attempt: int = 1,
        allow_recovery: bool = True,
        allow_intervention: bool = True,
    ) -> RunResult | RecoveryResume | None:
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
                        session=session,
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
                    request,
                    "policy_blocked",
                    decision.explanation,
                    False,
                    step.id,
                    session=session,
                )
            if decision.decision is Decision.REQUIRE_HUMAN_APPROVAL:
                if not allow_intervention:
                    return self._failure(
                        request,
                        "recovery_requires_human",
                        "A recovery action requires human approval and cannot run autonomously.",
                        False,
                        step.id,
                        session=session,
                    )
                return self._intervene(
                    request,
                    session,
                    step.id,
                    decision.reason_code,
                    observation,
                    lease_version,
                    decision.explanation,
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
            declared_failure = self._detect_failure(
                request.artifact, step, session, outputs, inputs
            )
            if declared_failure is not None:
                return self._application_failure(request, session, step.id, declared_failure)
            for condition in step.postconditions:
                if not session.wait_until(condition, outputs, inputs, step.timeout_ms):
                    recovery = (
                        self._attempt_recovery(
                            request,
                            session,
                            step,
                            inputs,
                            outputs,
                            recovery_uses,
                            lease_version,
                        )
                        if allow_recovery
                        else None
                    )
                    if recovery is not None:
                        return recovery
                    declared_failure = self._detect_failure(
                        request.artifact, step, session, outputs, inputs
                    )
                    if declared_failure is not None:
                        return self._application_failure(
                            request, session, step.id, declared_failure
                        )
                    return self._failure(
                        request,
                        "postcondition_mismatch",
                        "The action completed but its declared effect was not observed.",
                        False,
                        step.id,
                        session=session,
                    )
                outcome = self._detect_outcome(request.artifact, step, session, outputs, inputs)
                if outcome is not None:
                    return self._business_outcome(request, outcome, inputs)
            return None
        except SurfaceError as error:
            can_retry = (
                error.recoverable
                and error.effect_absent
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
                    recovery_uses,
                    lease_version,
                    attempt + 1,
                    allow_recovery,
                    allow_intervention,
                )
            if allow_recovery:
                recovery = self._attempt_recovery(
                    request,
                    session,
                    step,
                    inputs,
                    outputs,
                    recovery_uses,
                    lease_version,
                )
                if recovery is not None:
                    return recovery
            return self._surface_failure(request, session, step.id, error, lease_version)

    def _attempt_recovery(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step: Step,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        recovery_uses: dict[str, int],
        lease_version: int,
    ) -> RunResult | RecoveryResume | None:
        indexed = {recovery.id: recovery for recovery in request.artifact.recoveries}
        for recovery_id in step.recovery_refs:
            recovery = indexed[recovery_id]
            if not session.evaluate(recovery.trigger, outputs, inputs):
                continue
            uses = recovery_uses.get(recovery.id, 0)
            if uses >= recovery.max_uses:
                self.recorder.record(
                    "recovery_exhausted",
                    request.run_id,
                    step_id=step.id,
                    details={"recovery_id": recovery.id, "uses": uses},
                )
                return self._failure(
                    request,
                    "recovery_exhausted",
                    "A declared recovery did not restore the expected UI state.",
                    False,
                    step.id,
                    session=session,
                )
            recovery_uses[recovery.id] = uses + 1
            self.recorder.record(
                "recovery_started",
                request.run_id,
                step_id=step.id,
                details={"recovery_id": recovery.id, "use": uses + 1},
            )
            result = self._execute_recovery(
                request,
                session,
                recovery,
                inputs,
                outputs,
                recovery_uses,
                lease_version,
            )
            if result is not None:
                return result
            self.recorder.record(
                "recovery_completed",
                request.run_id,
                step_id=step.id,
                details={"recovery_id": recovery.id, "resume_at": recovery.resume_at},
            )
            return RecoveryResume(recovery.resume_at)
        return None

    def _execute_recovery(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        recovery: Recovery,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        recovery_uses: dict[str, int],
        lease_version: int,
    ) -> RunResult | None:
        for recovery_step in recovery.steps:
            result = self._execute_step(
                request,
                session,
                recovery_step,
                inputs,
                outputs,
                recovery_uses,
                lease_version,
                allow_recovery=False,
                allow_intervention=False,
            )
            if isinstance(result, RecoveryResume):
                raise RuntimeError("nested recovery control flow is not allowed")
            if result is not None:
                return result
        return None

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

    @staticmethod
    def _detect_failure(
        artifact: CapabilityArtifact,
        step: Step,
        session: SurfaceSession,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
    ) -> ApplicationFailure | None:
        indexed = {failure.code: failure for failure in artifact.failures}
        for code in step.failure_refs:
            failure = indexed[code]
            if session.evaluate(failure.detect, outputs, inputs):
                return failure
        return None

    def _application_failure(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step_id: str,
        failure: ApplicationFailure,
    ) -> FailureResult:
        return self._failure(
            request,
            failure.code,
            failure.description,
            failure.recoverable,
            step_id,
            expected={"state": failure.expected_state},
            observed={"state": failure.observed_state},
            session=session,
        )

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

    def _complete(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> RunResult:
        if not session.wait_until(request.artifact.checkpoint.condition, outputs, inputs, 10_000):
            return self._failure(
                request,
                "checkpoint_mismatch",
                "The final success checkpoint was not satisfied.",
                False,
                session=session,
            )
        try:
            validated_outputs = validate_object(request.artifact.outputs, outputs)
        except ContractValidationError as error:
            return self._failure(
                request,
                "output_validation_failed",
                str(error),
                False,
                session=session,
            )
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

    def _retain_continuation(
        self,
        result: InterventionRequiredResult,
        request: ReplayRequest,
        session: SurfaceSession,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        recovery_uses: dict[str, int],
        interrupted_step_index: int,
    ) -> None:
        if self.continuation_sink is None:
            return
        observation = session.observe()
        self.continuation_sink(
            ReplayContinuation(
                intervention_id=result.intervention_id,
                request=request,
                session=session,
                inputs=dict(inputs),
                outputs=dict(outputs),
                recovery_uses=dict(recovery_uses),
                interrupted_step_index=interrupted_step_index,
                initial_fingerprint=observation.fingerprint,
            )
        )

    @staticmethod
    def _assert_continuation(continuation: ReplayContinuation) -> None:
        if not 0 <= continuation.interrupted_step_index < len(continuation.request.artifact.steps):
            raise ValueError("continuation step index is outside the artifact")

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
                error.safe_message,
            )
        return self._failure(
            request,
            error.code,
            error.safe_message,
            error.recoverable,
            step_id,
            error.expected,
            error.observed,
            session,
        )

    def _intervene(
        self,
        request: ReplayRequest,
        session: SurfaceSession,
        step_id: str | None,
        code: str,
        observation: NormalizedObservation,
        lease_version: int,
        explanation: str | None = None,
    ) -> InterventionRequiredResult:
        from replayforge.shared.ids import EntityKind, new_id

        self._attach_handoff_frame(request.run_id, session, "handoff-before")
        intervention_id = new_id(EntityKind.INTERVENTION)
        routed_id = self.intervention_router.open(
            intervention_id=intervention_id,
            run_id=request.run_id,
            session_id=session.session_id,
            expected_lease_version=lease_version,
            code=code,
            step_id=step_id,
            observation=observation,
            explanation=explanation,
            context=InterventionContext(
                run_mode=InterventionRunMode.REPLAY,
                application_family=request.artifact.capability.application_family,
                tenant=request.tenant,
                task_summary=request.artifact.capability.description,
                capability_id=request.artifact.capability.id,
                capability_version=request.artifact.capability.version,
                capability_name=request.artifact.capability.name,
                step_id=step_id,
                surface_route=observation.route,
            ),
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

    def _attach_handoff_frame(
        self,
        run_id: str,
        session: SurfaceSession,
        kind: str,
    ) -> None:
        frame = session.capture_sanitized_evidence_frame()
        self.recorder.attach_sanitized(
            kind,
            SanitizedEvidence(frame.content, "image/png", frame.redaction_directives),
            RetentionClass.HUMAN_AUDIT,
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
        session: SurfaceSession | None = None,
    ) -> FailureResult:
        evidence_frame = "not_applicable"
        if session is not None:
            try:
                self._attach_failure_frame(request.run_id, session)
                evidence_frame = "captured"
            except (OSError, RuntimeError, ValueError):
                evidence_frame = "unavailable"
        self.recorder.record(
            "replay_failed",
            request.run_id,
            step_id=step_id,
            details={"code": code, "evidence_frame": evidence_frame},
        )
        return FailureResult(
            status="failure",
            run_id=request.run_id,
            code=code,
            message=message,
            recoverable=recoverable,
            step_id=step_id,
            expected=cast(dict[str, JsonValue] | None, expected),
            observed=cast(dict[str, JsonValue] | None, observed),
            evidence_manifest=self.recorder.evidence_manifest_key,
        )

    def _attach_failure_frame(self, run_id: str, session: SurfaceSession) -> None:
        frame = session.capture_sanitized_evidence_frame()
        self.recorder.attach_sanitized(
            "failure-state",
            SanitizedEvidence(frame.content, "image/png", frame.redaction_directives),
            RetentionClass.FAILURE,
        )

    @staticmethod
    def _transform(value: str, transform: str) -> str:
        if transform == "lowercase":
            return value.strip().casefold()
        if transform in {"trim", "decimal", "date-time"}:
            return value.strip().removeprefix("$").replace(",", "")
        return value
