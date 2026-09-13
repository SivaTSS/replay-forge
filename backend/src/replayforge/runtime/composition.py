"""Composition root for the local deterministic replay runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from replayforge.api.services import ApiServices
from replayforge.capabilities.assets import LocalCapabilityAssetStore
from replayforge.capabilities.models import BusinessOutcome
from replayforge.capabilities.registry import (
    CapabilityRegistry,
    CapabilityVersionRecord,
    InMemoryCapabilityRegistry,
)
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.compiler import SavingsBalanceCompiler
from replayforge.discovery.engine import DiscoveryEngine, DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.redaction import StructuredRedactor
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.models import (
    ControlOwner,
    HumanInputCommand,
    HumanInputConflictError,
    HumanInputReceipt,
    InterventionFrame,
    InterventionStatus,
    OwnerKind,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionCoordinator,
    InterventionResume,
    InterventionTransition,
)
from replayforge.observability.model_calls import (
    LangfuseModelCallTelemetry,
    ModelCallTelemetry,
    NoOpModelCallTelemetry,
)
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import DataClassification, Risk
from replayforge.providers.openai import OpenAIModelProvider
from replayforge.replay.engine import (
    ReplayContinuation,
    ReplayEngine,
    ReplayRequest,
    ResumeValidationError,
)
from replayforge.runs.discovery_service import DiscoveryApplicationService, DiscoveryExecutor
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.runs.results import (
    FailureResult,
    InterventionRequiredResult,
    RunResult,
)
from replayforge.runs.service import ReplayApplicationService, ReplayExecutor
from replayforge.runtime.worker import SerialSessionWorker
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.models import HumanInput, SurfaceError, SurfaceFrame, Viewport
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver
from replayforge.surfaces.vision import RapidOcrTextRecognizer, VisionGrounder

_ALLOWED_ROUTES = frozenset({"/members/search", "/accounts/:account_id/details"})
_PLATFORM_ACTIONS = frozenset({"type", "click", "select", "extract", "wait_for", "assert"})


def load_registry(directory: Path) -> CapabilityRegistry:
    registry = InMemoryCapabilityRegistry(SystemClock())
    paths = sorted(directory.glob("*/*.yaml"))
    if not paths:
        raise ValueError("artifact directory contains no versioned YAML artifacts")
    for path in paths:
        if path.stat().st_size > 1_000_000:
            raise ValueError("artifact exceeds the one-megabyte startup limit")
        registry.publish(load_artifact_yaml(path.read_text(encoding="utf-8")))
    return registry


def origin_ready(origin: str) -> bool:
    """Treat an HTTP-speaking origin as reachable even when its root route is client-invalid."""
    try:
        with urlopen(origin, timeout=1) as response:
            return int(response.status) < 500
    except HTTPError as error:
        return int(error.code) < 500
    except (OSError, URLError):
        return False


def effective_replay_policy(record: CapabilityVersionRecord, origin: str) -> EffectivePolicy:
    artifact = record.artifact
    capability_actions = artifact.policy.allowed_action_types
    layers = (
        PolicyLayer(
            "platform", frozenset({origin}), _ALLOWED_ROUTES, _PLATFORM_ACTIONS, Risk.SENSITIVE
        ),
        PolicyLayer(
            "application", frozenset({origin}), _ALLOWED_ROUTES, _PLATFORM_ACTIONS, Risk.SENSITIVE
        ),
        PolicyLayer(
            "tenant", frozenset({origin}), _ALLOWED_ROUTES, _PLATFORM_ACTIONS, Risk.SENSITIVE
        ),
        PolicyLayer(
            "capability",
            frozenset({origin}),
            _ALLOWED_ROUTES,
            capability_actions,
            artifact.policy.maximum_risk,
        ),
        PolicyLayer(
            "invocation",
            frozenset({origin}),
            _ALLOWED_ROUTES,
            capability_actions,
            artifact.policy.maximum_risk,
        ),
    )
    return EffectivePolicy.intersect(*layers)


class RetainedSurfaceDriver(Protocol):
    def close(self) -> None: ...

    def capture_active_frame(self) -> SurfaceFrame: ...

    def execute_active_human_input(self, action: HumanInput) -> None: ...


@dataclass(slots=True)
class LiveBrowserSession:
    worker: SerialSessionWorker
    driver: RetainedSurfaceDriver
    frame_sequence: int = 0
    latest_frame: InterventionFrame | None = None
    last_client_sequence: int = 0

    def close(self) -> None:
        self.worker.close(self.driver.close)

    def capture_frame(self) -> InterventionFrame:
        frame = self.worker.call(self.driver.capture_active_frame)
        self.frame_sequence += 1
        self.latest_frame = InterventionFrame(
            frame.content,
            self.frame_sequence,
            frame.viewport,
            self.last_client_sequence + 1,
        )
        return self.latest_frame

    def validate_input(self, command: HumanInputCommand) -> None:
        if command.client_sequence != self.last_client_sequence + 1:
            raise HumanInputConflictError("client input sequence is stale or out of order")
        if self.latest_frame is None or command.source_frame_sequence != self.latest_frame.sequence:
            raise HumanInputConflictError("source frame is stale or unavailable")
        if command.viewport != self.latest_frame.viewport:
            raise HumanInputConflictError("source viewport dimensions are stale")

    def invalidate_frame(self) -> None:
        self.latest_frame = None

    def apply_input(self, command: HumanInputCommand) -> HumanInputReceipt:
        self.validate_input(command)
        self.last_client_sequence = command.client_sequence
        self.latest_frame = None
        self.worker.call(lambda: self.driver.execute_active_human_input(command.action))
        return HumanInputReceipt(command.client_sequence, command.source_frame_sequence)


@dataclass(frozen=True, slots=True)
class ManagedReplayContinuation:
    validate: Callable[[], BusinessOutcome | None]
    resume: Callable[[int, BusinessOutcome | None], RunResult]


@dataclass(slots=True)
class ManagedReplayExecutor:
    engine: ReplayEngine
    driver: PlaywrightSurfaceDriver
    worker: SerialSessionWorker
    live_sessions: dict[str, LiveBrowserSession]
    lock: Lock

    def execute(self, request: ReplayRequest) -> RunResult:
        try:
            result = self.worker.call(lambda: self.engine.execute(request))
        except BaseException:
            self.worker.close(self.driver.close)
            raise
        if isinstance(result, InterventionRequiredResult):
            with self.lock:
                self.live_sessions[result.intervention_id] = LiveBrowserSession(
                    self.worker, self.driver
                )
        else:
            self.worker.close(self.driver.close)
        return result


@dataclass(slots=True)
class ManagedDiscoveryExecutor:
    engine: DiscoveryEngine
    driver: PlaywrightSurfaceDriver
    worker: SerialSessionWorker
    live_sessions: dict[str, LiveBrowserSession]
    lock: Lock

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        try:
            result = self.worker.call(lambda: self.engine.execute(request))
        except BaseException:
            self.worker.close(self.driver.close)
            raise
        if isinstance(result, InterventionRequiredResult):
            with self.lock:
                self.live_sessions[result.intervention_id] = LiveBrowserSession(
                    self.worker, self.driver
                )
        else:
            self.worker.close(self.driver.close)
        return result


@dataclass(slots=True)
class RuntimeInterventionService:
    coordinator: InterventionCoordinator
    live_sessions: dict[str, LiveBrowserSession]
    journals: dict[str, InMemoryRunJournal]
    lock: Lock
    replay_continuations: dict[str, ManagedReplayContinuation] = field(default_factory=dict)
    replay_result_finalizer: Callable[[RunResult], RunResult] | None = None

    def get(self, intervention_id: str) -> InterventionTransition:
        return self.coordinator.get(intervention_id)

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        with self.lock:
            transition = self.coordinator.claim(
                intervention_id, expected_lease_version, operator_id
            )
            self._invalidate_frame(intervention_id)
            return transition

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        with self.lock:
            transition = self.coordinator.release(
                intervention_id, expected_lease_version, operator_id
            )
            self._invalidate_frame(intervention_id)
            return transition

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionResume:
        with self.lock:
            started = self.coordinator.begin_resume(
                intervention_id, expected_lease_version, operator_id
            )
            self._invalidate_frame(intervention_id)
            session = self.live_sessions.get(intervention_id)
            managed = self.replay_continuations.get(intervention_id)
            journal = self.journals.get(str(started.intervention.run_id))
        if session is None or journal is None:
            with self.lock:
                reopened = self.coordinator.reopen(
                    intervention_id,
                    "The retained session or run journal is unavailable.",
                )
            return InterventionResume(reopened)
        if managed is None:
            journal.record(
                "resume_rejected",
                str(started.intervention.run_id),
                details={"code": "continuation_unavailable"},
            )
            with self.lock:
                reopened = self.coordinator.reopen(
                    intervention_id,
                    "A deterministic continuation is unavailable for this intervention.",
                )
            return InterventionResume(reopened)
        try:
            outcome = session.worker.call(managed.validate)
        except ResumeValidationError as error:
            journal.record(
                "resume_rejected",
                str(started.intervention.run_id),
                details={"code": error.code},
            )
            with self.lock:
                reopened = self.coordinator.reopen(intervention_id, error.safe_message)
            return InterventionResume(reopened)

        with self.lock:
            resumed = self.coordinator.complete_resume(
                intervention_id,
                started.lease.version,
                "Fresh state satisfied the interrupted step contract.",
            )
            self.replay_continuations.pop(intervention_id, None)
        journal.record(
            "automation_resumed",
            str(started.intervention.run_id),
            details={"lease_version": resumed.lease.version},
        )
        try:
            result = session.worker.call(lambda: managed.resume(resumed.lease.version, outcome))
        except BaseException:
            journal.record(
                "resume_failed",
                str(started.intervention.run_id),
                details={"code": "resume_execution_failed"},
            )
            result = FailureResult(
                status="failure",
                run_id=str(started.intervention.run_id),
                code="resume_execution_failed",
                message="Automation could not continue after validated human handoff.",
                recoverable=False,
                evidence_manifest=journal.evidence_manifest_key,
            )
        if isinstance(result, InterventionRequiredResult):
            with self.lock:
                retained = self.live_sessions.pop(intervention_id)
                self.live_sessions[result.intervention_id] = retained
            return InterventionResume(resumed, result)

        try:
            finalized = (
                self.replay_result_finalizer(result)
                if self.replay_result_finalizer is not None
                else result
            )
        except BaseException:
            self._close_session(intervention_id)
            raise
        self._close_session(intervention_id)
        return InterventionResume(resumed, finalized)

    def _close_session(self, intervention_id: str) -> None:
        with self.lock:
            completed_session = self.live_sessions.get(intervention_id)
            if completed_session is not None:
                del self.live_sessions[intervention_id]
        if completed_session is not None:
            completed_session.close()

    def viewport(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionFrame:
        with self.lock:
            transition = self.coordinator.get(intervention_id)
            if (
                transition.intervention.status is not InterventionStatus.CLAIMED
                or transition.intervention.operator_id != operator_id
            ):
                raise InterventionAuthorizationError("operator does not own this intervention")
            self.coordinator.leases.assert_can_act(
                str(transition.intervention.session_id),
                expected_lease_version,
                ControlOwner(OwnerKind.HUMAN, operator_id),
            )
            session = self.live_sessions.get(intervention_id)
            if session is None:
                raise InterventionAuthorizationError("live intervention session is unavailable")
            frame = session.capture_frame()
        if len(frame.content) > 5 * 1024 * 1024 or not frame.content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise RuntimeError("live viewport frame violates its media contract")
        return frame

    def heartbeat(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        with self.lock:
            transition = self.coordinator.heartbeat(
                intervention_id, expected_lease_version, operator_id
            )
            self._invalidate_frame(intervention_id)
            return transition

    def send_input(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str,
        command: HumanInputCommand,
    ) -> HumanInputReceipt:
        with self.lock:
            transition = self.coordinator.get(intervention_id)
            if (
                transition.intervention.status is not InterventionStatus.CLAIMED
                or transition.intervention.operator_id != operator_id
            ):
                raise InterventionAuthorizationError("operator does not own this intervention")
            self.coordinator.leases.assert_can_act(
                str(transition.intervention.session_id),
                expected_lease_version,
                ControlOwner(OwnerKind.HUMAN, operator_id),
            )
            session = self.live_sessions.get(intervention_id)
            journal = self.journals.get(str(transition.intervention.run_id))
            if session is None or journal is None:
                raise InterventionAuthorizationError("live intervention session is unavailable")
            session.validate_input(command)
            details = {
                **command.audit_details(),
                "intervention_id": intervention_id,
                "operator_id": operator_id,
                "session_id": str(transition.intervention.session_id),
            }
            journal.record(
                "human_input_dispatched", str(transition.intervention.run_id), details=details
            )
            try:
                receipt = session.apply_input(command)
            except BaseException as error:
                failure_details = {
                    **details,
                    "error_code": (
                        error.code if isinstance(error, SurfaceError) else "input_dispatch_failed"
                    ),
                }
                journal.record(
                    "human_input_failed",
                    str(transition.intervention.run_id),
                    details=failure_details,
                )
                raise
            journal.record(
                "human_input_applied", str(transition.intervention.run_id), details=details
            )
            return receipt

    def _invalidate_frame(self, intervention_id: str) -> None:
        session = self.live_sessions.get(intervention_id)
        if session is not None:
            session.invalidate_frame()

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition:
        with self.lock:
            transition = self.coordinator.terminate(
                intervention_id, expected_lease_version, operator_id, resolution
            )
            session = self.live_sessions.pop(intervention_id, None)
        if session is not None:
            session.close()
        return transition


@dataclass(slots=True)
class LocalRuntime:
    service: ReplayApplicationService
    discovery_service: DiscoveryApplicationService
    intervention_service: RuntimeInterventionService
    journals: dict[str, InMemoryRunJournal]
    interventions: InMemoryInterventionRouter
    live_sessions: dict[str, LiveBrowserSession]
    model_telemetry: ModelCallTelemetry
    _lock: Lock = field(repr=False)

    @property
    def api_services(self) -> ApiServices:
        return ApiServices(self.service, self.discovery_service, self.intervention_service)

    def close(self) -> None:
        with self._lock:
            sessions = tuple(self.live_sessions.values())
            self.live_sessions.clear()
        for session in sessions:
            session.close()
        self.model_telemetry.close()


def build_runtime(settings: object) -> LocalRuntime:
    from replayforge.runtime.settings import RuntimeSettings

    if not isinstance(settings, RuntimeSettings):
        raise TypeError("settings must be RuntimeSettings")
    clock = SystemClock()
    registry = load_registry(settings.artifact_directory)
    capability_assets = LocalCapabilityAssetStore(settings.capability_asset_directory)
    text_recognizer = RapidOcrTextRecognizer()
    evidence_store = LocalEvidenceStore(settings.evidence_directory, clock)
    configured_secrets = tuple(
        secret.get_secret_value()
        for secret in (settings.openai_api_key, settings.langfuse_secret_key)
        if secret is not None
    )
    lease_service = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    interventions = InMemoryInterventionRouter(clock)
    journals: dict[str, InMemoryRunJournal] = {}
    result_classifications: dict[str, dict[str, DataClassification]] = {}
    live_sessions: dict[str, LiveBrowserSession] = {}
    replay_continuations: dict[str, ManagedReplayContinuation] = {}
    lock = Lock()

    def executor_factory(run_id: str, record: CapabilityVersionRecord) -> ReplayExecutor:
        journal = InMemoryRunJournal(
            run_id,
            clock,
            StructuredRedactor(configured_secrets=configured_secrets),
            evidence_store,
        )
        with lock:
            journals[run_id] = journal
            result_classifications[run_id] = {
                **{
                    f"outputs.{name}": schema.data_classification
                    for name, schema in record.artifact.outputs.properties.items()
                },
                "expected": DataClassification.PERSONAL,
                "observed": DataClassification.PERSONAL,
            }
        driver = PlaywrightSurfaceDriver(
            settings.demo_base_url,
            settings.browser_headless,
            VisionGrounder(text_recognizer, capability_assets, settings.vision_policy),
            viewport=Viewport(settings.browser_viewport_width, settings.browser_viewport_height),
        )
        worker = SerialSessionWorker(run_id)
        engine: ReplayEngine

        def retain_continuation(continuation: ReplayContinuation) -> None:
            with lock:
                replay_continuations[continuation.intervention_id] = ManagedReplayContinuation(
                    validate=lambda: engine.validate_resume(continuation),
                    resume=lambda lease_version, outcome: engine.resume(
                        continuation, lease_version, outcome
                    ),
                )

        engine = ReplayEngine(
            driver,
            PolicyEvaluator(clock),
            effective_replay_policy(record, settings.demo_base_url),
            lease_service,
            journal,
            interventions,
            continuation_sink=retain_continuation,
        )
        return ManagedReplayExecutor(engine, driver, worker, live_sessions, lock)

    def target_ready() -> bool:
        return origin_ready(settings.demo_base_url)

    def finalize_replay(result: RunResult) -> RunResult:
        if isinstance(result, InterventionRequiredResult):
            return result
        with lock:
            journal = journals[result.run_id]
            classifications = result_classifications[result.run_id]
        manifest_key = journal.finalize(result.model_dump(mode="json"), classifications)
        with lock:
            result_classifications.pop(result.run_id, None)
        return result.model_copy(update={"evidence_manifest": manifest_key})

    service = ReplayApplicationService(registry, executor_factory, (target_ready,), finalize_replay)
    model_telemetry: ModelCallTelemetry = (
        LangfuseModelCallTelemetry.create(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            policy=settings.model_policy,
        )
        if settings.langfuse_public_key is not None and settings.langfuse_secret_key is not None
        else NoOpModelCallTelemetry()
    )
    provider = (
        OpenAIModelProvider.from_api_key(
            settings.openai_api_key.get_secret_value(), settings.model_policy, model_telemetry
        )
        if settings.openai_api_key is not None
        else None
    )

    def discovery_factory(run_id: str) -> DiscoveryExecutor:
        if provider is None:
            raise RuntimeError("discovery provider is not configured")
        journal = InMemoryRunJournal(
            run_id,
            clock,
            StructuredRedactor(configured_secrets=configured_secrets),
            evidence_store,
        )
        with lock:
            journals[run_id] = journal
            result_classifications[run_id] = {
                "expected": DataClassification.PERSONAL,
                "observed": DataClassification.PERSONAL,
            }
        driver = PlaywrightSurfaceDriver(
            settings.demo_base_url,
            settings.browser_headless,
            VisionGrounder(text_recognizer, capability_assets, settings.vision_policy),
            allow_transient_coordinates=True,
            viewport=Viewport(settings.browser_viewport_width, settings.browser_viewport_height),
        )
        worker = SerialSessionWorker(run_id)
        engine = DiscoveryEngine(
            driver,
            provider.for_run(),
            SavingsBalanceCompiler(clock),
            PolicyEvaluator(clock),
            EffectivePolicy.intersect(
                PolicyLayer(
                    "platform",
                    frozenset({settings.demo_base_url}),
                    _ALLOWED_ROUTES,
                    _PLATFORM_ACTIONS,
                    Risk.READ_ONLY,
                ),
                PolicyLayer(
                    "application",
                    frozenset({settings.demo_base_url}),
                    _ALLOWED_ROUTES,
                    frozenset({"type", "click", "extract"}),
                    Risk.READ_ONLY,
                ),
            ),
            lease_service,
            journal,
            interventions,
            clock,
        )
        return ManagedDiscoveryExecutor(engine, driver, worker, live_sessions, lock)

    def finalize_discovery(result: DiscoveryResult) -> DiscoveryResult:
        if isinstance(result, InterventionRequiredResult):
            return result
        with lock:
            journal = journals[result.run_id]
            classifications = result_classifications[result.run_id]
        if isinstance(result, DiscoverySuccess):
            payload: dict[str, object] = {
                "artifact": result.artifact.model_dump(mode="json"),
                "evidence_manifest": result.evidence_manifest,
                "run_id": result.run_id,
                "status": result.status,
            }
            manifest_key = journal.finalize(payload, classifications)
            with lock:
                result_classifications.pop(result.run_id, None)
            return dataclass_replace(result, evidence_manifest=manifest_key)
        if not isinstance(result, FailureResult):
            raise TypeError("unsupported completed discovery result")
        manifest_key = journal.finalize(result.model_dump(mode="json"), classifications)
        with lock:
            result_classifications.pop(result.run_id, None)
        return result.model_copy(update={"evidence_manifest": manifest_key})

    discovery_service = DiscoveryApplicationService(
        registry,
        discovery_factory,
        lambda: provider is not None and model_telemetry.ready() and target_ready(),
        finalize_discovery,
    )
    intervention_service = RuntimeInterventionService(
        InterventionCoordinator(interventions, lease_service),
        live_sessions,
        journals,
        lock,
        replay_continuations,
        finalize_replay,
    )
    return LocalRuntime(
        service,
        discovery_service,
        intervention_service,
        journals,
        interventions,
        live_sessions,
        model_telemetry,
        lock,
    )
