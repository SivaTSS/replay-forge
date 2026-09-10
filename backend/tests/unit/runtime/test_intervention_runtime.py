from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, get_ident

import pytest

from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
    LeaseConflictError,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionCoordinator,
)
from replayforge.runtime.composition import LiveBrowserSession, RuntimeInterventionService
from replayforge.runtime.worker import SerialSessionWorker
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, SurfaceFrame, Viewport


@dataclass
class FakeRetainedDriver:
    frame: bytes = b"\x89PNG\r\n\x1a\nframe"
    captured_on: int | None = None
    closed_on: int | None = None

    def capture_active_frame(self) -> SurfaceFrame:
        self.captured_on = get_ident()
        return SurfaceFrame(self.frame, Viewport(1280, 800))

    def close(self) -> None:
        self.closed_on = get_ident()


def runtime_service() -> tuple[RuntimeInterventionService, str, FakeRetainedDriver, int]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock)
    session_id = str(new_id(EntityKind.SESSION))
    initial = leases.create_for_automation(session_id)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    paused = leases.pause(session_id, initial.version, intervention_id)
    router.create(
        intervention_id=intervention_id,
        run_id=str(new_id(EntityKind.RUN)),
        session_id=session_id,
        code="unexpected_dialog",
        step_id=None,
        observation=NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=paused.session_id,
            captured_at=clock.now(),
            route="/members/search",
            viewport=Viewport(1280, 800),
            fingerprint="state",
            landmarks=(),
        ),
    )
    worker = SerialSessionWorker("intervention-test")
    driver = FakeRetainedDriver()
    live = {intervention_id: LiveBrowserSession(worker, driver)}
    return (
        RuntimeInterventionService(InterventionCoordinator(router, leases), live, Lock()),
        intervention_id,
        driver,
        worker.call(get_ident),
    )


def test_viewport_requires_current_human_lease_and_uses_owner_thread() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    claimed = service.claim(intervention_id, 2, "operator-7")
    try:
        frame = service.viewport(intervention_id, claimed.lease.version, "operator-7")

        assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert frame.sequence == 1
        assert frame.viewport == Viewport(1280, 800)
        assert driver.captured_on == owner_thread
        with pytest.raises(InterventionAuthorizationError):
            service.viewport(intervention_id, claimed.lease.version, "operator-8")
        with pytest.raises(LeaseConflictError):
            service.viewport(intervention_id, claimed.lease.version - 1, "operator-7")
        heartbeat = service.heartbeat(intervention_id, claimed.lease.version, "operator-7")
        with pytest.raises(LeaseConflictError):
            service.viewport(intervention_id, claimed.lease.version, "operator-7")
        next_frame = service.viewport(intervention_id, heartbeat.lease.version, "operator-7")
        assert next_frame.content.startswith(b"\x89PNG")
        assert next_frame.sequence == 2
    finally:
        service.terminate(
            intervention_id,
            service.get(intervention_id).lease.version,
            "operator-7",
            "Test complete.",
        )
    assert driver.closed_on == owner_thread


def test_viewport_rejects_unclaimed_intervention() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    try:
        with pytest.raises(InterventionAuthorizationError):
            service.viewport(intervention_id, 2, "operator-7")
    finally:
        service.terminate(intervention_id, 2, None, "Test complete.")
    assert driver.closed_on is not None


def test_viewport_rejects_invalid_frame_contract() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    claimed = service.claim(intervention_id, 2, "operator-7")
    driver.frame = b"not-a-png"
    try:
        with pytest.raises(RuntimeError, match="media contract"):
            service.viewport(intervention_id, claimed.lease.version, "operator-7")
    finally:
        service.terminate(
            intervention_id,
            claimed.lease.version,
            "operator-7",
            "Test complete.",
        )
