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
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.policy.evaluator import PolicyEvaluator
from replayforge.policy.models import EffectivePolicy, PolicyLayer
from replayforge.policy.types import Risk
from replayforge.replay.engine import ReplayEngine, ReplayRequest
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
class LocalRuntime:
    service: ReplayApplicationService
    journals: dict[str, InMemoryRunJournal]
    interventions: InMemoryInterventionRouter
    live_drivers: dict[str, PlaywrightSurfaceDriver]
    _lock: Lock = field(repr=False)

    @property
    def api_services(self) -> ApiServices:
        return ApiServices(self.service)

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
    return LocalRuntime(service, journals, interventions, live_drivers, lock)
