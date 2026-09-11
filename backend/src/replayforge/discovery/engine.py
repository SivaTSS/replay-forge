"""Bounded observe-decide-act orchestration for capability discovery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from replayforge.capabilities.models import ExtractAction
from replayforge.capabilities.values import ContractValidationError, validate_object
from replayforge.discovery.models import (
    ActProposal,
    CompleteProposal,
    DiscoveryResult,
    DiscoverySuccess,
    EscalateProposal,
    ProviderContext,
    RecordedDiscoveryStep,
)
from replayforge.discovery.ports import ArtifactCompiler, ModelProvider, ModelProviderError
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
    max_steps: int = 20
    timeout: timedelta = timedelta(minutes=5)
    max_repeated_state: int = 2
    max_repeated_action: int = 2
    minimum_confidence: float = 0.6

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("discovery goal is required")
        if self.max_steps < 1 or self.timeout <= timedelta(0):
            raise ValueError("discovery budgets must be positive")
        if self.max_repeated_state < 1 or self.max_repeated_action < 1:
            raise ValueError("stuck-detection limits must be positive")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("minimum confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class DiscoveryEngine:
    surface_driver: SurfaceDriver
    model_provider: ModelProvider
    artifact_compiler: ArtifactCompiler
    policy_evaluator: PolicyEvaluator
    effective_policy: EffectivePolicy
    lease_service: ControlLeaseService
    recorder: RunRecorder
    intervention_router: InterventionRouter
    clock: Clock

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        session: SurfaceSession | None = None
        preserve_session = False
        started_at = self.clock.now()
        try:
            session = self.surface_driver.open(
                request.application_family, request.tenant, request.entry_point
            )
            lease = self.lease_service.create_for_automation(session.session_id)
            self.recorder.record("discovery_started", request.run_id)
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
                self.lease_service.assert_can_act(
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
                        allowed_action_types=self.effective_policy.allowed_action_types,
                        required_output_names=self.artifact_compiler.required_output_names,
                    )
                )
                self.recorder.record("model_proposal_received", request.run_id)
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
                    artifact = self.artifact_compiler.compile(
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

                act_result = self._act(
                    request, session, lease.version, proposal, observation, outputs
                )
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

    def _act(
        self,
        request: DiscoveryRequest,
        session: SurfaceSession,
        lease_version: int,
        proposal: ActProposal,
        before: NormalizedObservation,
        outputs: dict[str, Any],
    ) -> tuple[RecordedDiscoveryStep, str] | FailureResult | InterventionRequiredResult:
        target = session.resolve(proposal.target, 10_000) if proposal.target else None
        stable_target = session.capture_locator(target) if target else None
        decision = self.policy_evaluator.evaluate(
            self.effective_policy,
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
        after = session.observe()
        self.recorder.record("action_result", request.run_id)
        recorded = RecordedDiscoveryStep(
            action=proposal.action,
            target=stable_target,
            observation_before=before,
            observation_after=after,
            expected_effect=proposal.expected_effect,
            rationale=proposal.rationale,
            risk=proposal.declared_risk,
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
    def _transform(value: str, transform: str) -> str:
        if transform == "lowercase":
            return value.strip().lower()
        if transform == "decimal":
            return value.strip().removeprefix("$").replace(",", "")
        if transform in {"trim", "date-time"}:
            return value.strip()
        return value
