"""Composition root for the local deterministic replay runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from urllib.error import URLError
from urllib.request import urlopen

from replayforge.api.services import ApiServices
from replayforge.capabilities.registry import (
    CapabilityRegistry,
    CapabilityVersionRecord,
    InMemoryCapabilityRegistry,
)
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.compiler import SavingsBalanceCompiler
from replayforge.discovery.engine import DiscoveryEngine, DiscoveryRequest
from replayforge.discovery.models import DiscoveryResult
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import InterventionCoordinator, InterventionTransition
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import Risk
from replayforge.providers.openai import OpenAIModelProvider
from replayforge.replay.engine import ReplayEngine, ReplayRequest
from replayforge.runs.discovery_service import DiscoveryApplicationService, DiscoveryExecutor
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.runs.results import InterventionRequiredResult, RunResult
from replayforge.runs.service import ReplayApplicationService, ReplayExecutor
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver

_ALLOWED_ROUTES = frozenset({"/members/search", "/accounts/:account_id/details"})
_PLATFORM_ACTIONS = frozenset({"type", "click", "extract", "wait_for", "assert"})


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


@dataclass(slots=True)
class ManagedReplayExecutor:
    engine: ReplayEngine
    driver: PlaywrightSurfaceDriver
    live_drivers: dict[str, PlaywrightSurfaceDriver]
    lock: Lock

    def execute(self, request: ReplayRequest) -> RunResult:
        try:
            result = self.engine.execute(request)
        except BaseException:
            self.driver.close()
            raise
        if isinstance(result, InterventionRequiredResult):
            with self.lock:
                self.live_drivers[result.intervention_id] = self.driver
        else:
            self.driver.close()
        return result


@dataclass(slots=True)
class ManagedDiscoveryExecutor:
    engine: DiscoveryEngine
    driver: PlaywrightSurfaceDriver
    live_drivers: dict[str, PlaywrightSurfaceDriver]
    lock: Lock

    def execute(self, request: DiscoveryRequest) -> DiscoveryResult:
        try:
            result = self.engine.execute(request)
        except BaseException:
            self.driver.close()
            raise
        if isinstance(result, InterventionRequiredResult):
            with self.lock:
                self.live_drivers[result.intervention_id] = self.driver
        else:
            self.driver.close()
        return result


@dataclass(slots=True)
class RuntimeInterventionService:
    coordinator: InterventionCoordinator
    live_drivers: dict[str, PlaywrightSurfaceDriver]
    lock: Lock

    def get(self, intervention_id: str) -> InterventionTransition:
        return self.coordinator.get(intervention_id)

    def claim(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.coordinator.claim(intervention_id, expected_lease_version, operator_id)

    def release(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.coordinator.release(intervention_id, expected_lease_version, operator_id)

    def begin_resume(
        self, intervention_id: str, expected_lease_version: int, operator_id: str
    ) -> InterventionTransition:
        return self.coordinator.begin_resume(intervention_id, expected_lease_version, operator_id)

    def terminate(
        self,
        intervention_id: str,
        expected_lease_version: int,
        operator_id: str | None,
        resolution: str,
    ) -> InterventionTransition:
        transition = self.coordinator.terminate(
            intervention_id, expected_lease_version, operator_id, resolution
        )
        with self.lock:
            driver = self.live_drivers.pop(intervention_id, None)
        if driver is not None:
            driver.close()
        return transition


@dataclass(slots=True)
class LocalRuntime:
    service: ReplayApplicationService
    discovery_service: DiscoveryApplicationService
    intervention_service: RuntimeInterventionService
    journals: dict[str, InMemoryRunJournal]
    interventions: InMemoryInterventionRouter
    live_drivers: dict[str, PlaywrightSurfaceDriver]
    _lock: Lock = field(repr=False)

    @property
    def api_services(self) -> ApiServices:
        return ApiServices(self.service, self.discovery_service, self.intervention_service)

    def close(self) -> None:
        with self._lock:
            drivers = tuple(self.live_drivers.values())
            self.live_drivers.clear()
        for driver in drivers:
            driver.close()


def build_runtime(settings: object) -> LocalRuntime:
    from replayforge.runtime.settings import RuntimeSettings

    if not isinstance(settings, RuntimeSettings):
        raise TypeError("settings must be RuntimeSettings")
    clock = SystemClock()
    registry = load_registry(settings.artifact_directory)
    lease_service = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    interventions = InMemoryInterventionRouter(clock)
    journals: dict[str, InMemoryRunJournal] = {}
    live_drivers: dict[str, PlaywrightSurfaceDriver] = {}
    lock = Lock()

    def executor_factory(run_id: str, record: CapabilityVersionRecord) -> ReplayExecutor:
        journal = InMemoryRunJournal(run_id, clock)
        with lock:
            journals[run_id] = journal
        driver = PlaywrightSurfaceDriver(settings.demo_base_url, settings.browser_headless)
        engine = ReplayEngine(
            driver,
            PolicyEvaluator(clock),
            effective_replay_policy(record, settings.demo_base_url),
            lease_service,
            journal,
            interventions,
        )
        return ManagedReplayExecutor(engine, driver, live_drivers, lock)

    def target_ready() -> bool:
        try:
            with urlopen(settings.demo_base_url, timeout=1) as response:
                return int(response.status) < 500
        except (OSError, URLError):
            return False

    service = ReplayApplicationService(registry, executor_factory, (target_ready,))
    provider = (
        OpenAIModelProvider.from_api_key(
            settings.openai_api_key.get_secret_value(), settings.openai_model
        )
        if settings.openai_api_key is not None and settings.openai_model is not None
        else None
    )

    def discovery_factory(run_id: str) -> DiscoveryExecutor:
        if provider is None:
            raise RuntimeError("discovery provider is not configured")
        journal = InMemoryRunJournal(run_id, clock)
        with lock:
            journals[run_id] = journal
        driver = PlaywrightSurfaceDriver(settings.demo_base_url, settings.browser_headless)
        engine = DiscoveryEngine(
            driver,
            provider,
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
        return ManagedDiscoveryExecutor(engine, driver, live_drivers, lock)

    discovery_service = DiscoveryApplicationService(
        registry,
        discovery_factory,
        lambda: provider is not None and target_ready(),
    )
    intervention_service = RuntimeInterventionService(
        InterventionCoordinator(interventions, lease_service), live_drivers, lock
    )
    return LocalRuntime(
        service,
        discovery_service,
        intervention_service,
        journals,
        interventions,
        live_drivers,
        lock,
    )
