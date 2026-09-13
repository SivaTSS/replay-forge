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
from replayforge.interventions.models import (
    HumanInputCommand,
    HumanInputConflictError,
    InterventionContext,
    InterventionRunMode,
)
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import (
    InterventionAuthorizationError,
    InterventionCoordinator,
)
from replayforge.replay.engine import ResumeValidationError
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.runs.results import (
    CapabilityReference,
    FailureResult,
    RunResult,
    SuccessResult,
    VerifiedCheckpoint,
)
from replayforge.runtime.composition import (
    LiveBrowserSession,
    ManagedReplayContinuation,
    RuntimeInterventionService,
)
from replayforge.runtime.worker import SerialSessionWorker
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import (
    HumanPointerInput,
    HumanTextInput,
    NormalizedObservation,
    SurfaceFrame,
    Viewport,
)


@dataclass
class FakeRetainedDriver:
    frame: bytes = b"\x89PNG\r\n\x1a\nframe"
    captured_on: int | None = None
    closed_on: int | None = None
    input_on: int | None = None
    inputs: list[object] | None = None

    def capture_active_frame(self) -> SurfaceFrame:
        self.captured_on = get_ident()
        return SurfaceFrame(self.frame, Viewport(1280, 800))

    def close(self) -> None:
        self.closed_on = get_ident()

    def execute_active_human_input(self, action: object) -> None:
        self.input_on = get_ident()
        if self.inputs is not None:
            self.inputs.append(action)


def runtime_service() -> tuple[RuntimeInterventionService, str, FakeRetainedDriver, int]:
    clock = FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC))
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock, leases)
    session_id = str(new_id(EntityKind.SESSION))
    initial = leases.create_for_automation(session_id)
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    run_id = str(new_id(EntityKind.RUN))
    router.open(
        intervention_id=intervention_id,
        run_id=run_id,
        session_id=session_id,
        expected_lease_version=initial.version,
        code="unexpected_dialog",
        step_id=None,
        observation=NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=initial.session_id,
            captured_at=clock.now(),
            route="/members/search",
            viewport=Viewport(1280, 800),
            fingerprint="state",
            landmarks=(),
        ),
        context=InterventionContext(
            run_mode=InterventionRunMode.DISCOVERY,
            application_family="northstar",
            tenant="harbor",
            task_summary="Discovery run requires operator intervention.",
            surface_route="/members/search",
        ),
    )
    worker = SerialSessionWorker("intervention-test")
    driver = FakeRetainedDriver(inputs=[])
    live = {intervention_id: LiveBrowserSession(worker, driver)}
    return (
        RuntimeInterventionService(
            InterventionCoordinator(router, leases),
            live,
            {run_id: InMemoryRunJournal(run_id, clock)},
            Lock(),
        ),
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
        assert frame.next_client_sequence == 1
        assert service.viewport(intervention_id, claimed.lease.version, "operator-7") == frame
        assert frame.viewport == Viewport(1280, 800)
        assert driver.captured_on == owner_thread
        with pytest.raises(InterventionAuthorizationError):
            service.viewport(intervention_id, claimed.lease.version, "operator-8")
        with pytest.raises(LeaseConflictError):
            service.viewport(intervention_id, claimed.lease.version - 1, "operator-7")
        heartbeat = service.heartbeat(intervention_id, claimed.lease.version, "operator-7")
        with pytest.raises(LeaseConflictError):
            service.viewport(intervention_id, claimed.lease.version, "operator-7")
        with pytest.raises(HumanInputConflictError, match="source frame"):
            service.send_input(
                intervention_id,
                heartbeat.lease.version,
                "operator-7",
                HumanInputCommand(
                    client_sequence=frame.next_client_sequence,
                    source_frame_sequence=frame.sequence,
                    viewport=frame.viewport,
                    action=HumanPointerInput(100, 200),
                ),
            )
        next_frame = service.viewport(intervention_id, heartbeat.lease.version, "operator-7")
        assert next_frame.content.startswith(b"\x89PNG")
        assert next_frame.sequence == 2
        assert next_frame.next_client_sequence == 1
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


def test_human_input_requires_latest_frame_and_runs_on_owner_thread() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    claimed = service.claim(intervention_id, 2, "operator-7")
    try:
        frame = service.viewport(intervention_id, claimed.lease.version, "operator-7")
        command = HumanInputCommand(
            client_sequence=1,
            source_frame_sequence=frame.sequence,
            viewport=frame.viewport,
            action=HumanPointerInput(100, 200),
        )

        receipt = service.send_input(intervention_id, claimed.lease.version, "operator-7", command)

        assert receipt.client_sequence == 1
        assert driver.inputs == [HumanPointerInput(100, 200)]
        assert driver.input_on == owner_thread
        with pytest.raises(HumanInputConflictError, match="client input sequence"):
            service.send_input(intervention_id, claimed.lease.version, "operator-7", command)
    finally:
        service.terminate(
            intervention_id,
            service.get(intervention_id).lease.version,
            "operator-7",
            "Test complete.",
        )


def test_human_input_rejects_stale_frame_dimensions_and_redacts_text() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    claimed = service.claim(intervention_id, 2, "operator-7")
    try:
        frame = service.viewport(intervention_id, claimed.lease.version, "operator-7")
        wrong_viewport = HumanInputCommand(
            client_sequence=1,
            source_frame_sequence=frame.sequence,
            viewport=Viewport(640, 480),
            action=HumanTextInput("sensitive-member-value"),
        )
        with pytest.raises(HumanInputConflictError, match="viewport dimensions"):
            service.send_input(intervention_id, claimed.lease.version, "operator-7", wrong_viewport)

        command = HumanInputCommand(
            client_sequence=1,
            source_frame_sequence=frame.sequence,
            viewport=frame.viewport,
            action=HumanTextInput("sensitive-member-value"),
        )
        service.send_input(intervention_id, claimed.lease.version, "operator-7", command)

        events = next(iter(service.journals.values())).events()
        assert [event.event_type for event in events] == [
            "human_input_dispatched",
            "human_input_applied",
        ]
        assert all(event.details["character_count"] == 22 for event in events)
        assert "sensitive-member-value" not in repr(events)
        assert driver.inputs == [HumanTextInput("sensitive-member-value")]
    finally:
        service.terminate(
            intervention_id,
            service.get(intervention_id).lease.version,
            "operator-7",
            "Test complete.",
        )


def test_validated_resume_runs_continuation_and_closes_session() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    run_id = str(service.get(intervention_id).intervention.run_id)
    called_on: list[int] = []
    finalized: list[RunResult] = []

    def validate() -> None:
        called_on.append(get_ident())

    def resume(lease_version: int, outcome: object) -> SuccessResult:
        del outcome
        called_on.append(get_ident())
        assert lease_version == 5
        return SuccessResult(
            status="success",
            run_id=run_id,
            capability=CapabilityReference(id="member.lookup", version="1.0.0"),
            outputs={"balance": "1420.57"},
            checkpoint=VerifiedCheckpoint(id="balance_verified", verified=True),
            evidence_manifest=f"evidence://{run_id}/manifest.json",
        )

    def finalize(result: RunResult) -> RunResult:
        finalized.append(result)
        return result

    service.replay_continuations[intervention_id] = ManagedReplayContinuation(validate, resume)
    service.replay_result_finalizer = finalize
    claimed = service.claim(intervention_id, 2, "operator-7")

    completed = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert isinstance(completed.result, SuccessResult)
    assert completed.transition.intervention.status.value == "resolved"
    assert completed.transition.lease.owner.value == "automation"
    assert called_on == [owner_thread, owner_thread]
    assert finalized == [completed.result]
    assert driver.closed_on == owner_thread
    assert service.live_sessions == {}
    assert [event.event_type for event in service.journals[run_id].events()] == [
        "automation_resumed"
    ]


def test_failed_resume_validation_reopens_without_losing_session() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread

    def reject() -> None:
        raise ResumeValidationError(
            "resume_checkpoint_mismatch", "The expected state is not visible."
        )

    service.replay_continuations[intervention_id] = ManagedReplayContinuation(
        reject,
        lambda lease_version, outcome: pytest.fail(
            f"resume unexpectedly ran at lease {lease_version} with {outcome}"
        ),
    )
    claimed = service.claim(intervention_id, 2, "operator-7")

    reopened = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert reopened.result is None
    assert reopened.transition.intervention.status.value == "open"
    assert reopened.transition.intervention.explanation == "The expected state is not visible."
    assert reopened.transition.lease.owner.value == "automation_paused"
    assert intervention_id in service.live_sessions
    assert driver.closed_on is None
    service.terminate(
        intervention_id,
        reopened.transition.lease.version,
        None,
        "Test complete.",
    )


def test_resume_without_deterministic_continuation_reopens_safely() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    claimed = service.claim(intervention_id, 2, "operator-7")

    reopened = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert reopened.result is None
    assert reopened.transition.intervention.status.value == "open"
    assert "unavailable" in reopened.transition.intervention.explanation
    assert reopened.transition.lease.owner.value == "automation_paused"
    assert driver.closed_on is None
    run_id = str(reopened.transition.intervention.run_id)
    assert service.journals[run_id].events()[-1].details == {"code": "continuation_unavailable"}
    service.terminate(
        intervention_id,
        reopened.transition.lease.version,
        None,
        "Test complete.",
    )


def test_resume_without_retained_session_reopens_safely() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    retained = service.live_sessions.pop(intervention_id)
    claimed = service.claim(intervention_id, 2, "operator-7")

    reopened = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert reopened.result is None
    assert reopened.transition.intervention.status.value == "open"
    assert "session or run journal" in reopened.transition.intervention.explanation
    assert reopened.transition.lease.owner.value == "automation_paused"
    assert driver.closed_on is None
    retained.close()


def test_resume_execution_failure_is_sanitized_and_closes_session() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread

    def fail_resume(lease_version: int, outcome: object) -> RunResult:
        del lease_version, outcome
        raise RuntimeError("internal continuation failure")

    service.replay_continuations[intervention_id] = ManagedReplayContinuation(
        lambda: None,
        fail_resume,
    )
    claimed = service.claim(intervention_id, 2, "operator-7")

    completed = service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert isinstance(completed.result, FailureResult)
    assert completed.result.code == "resume_execution_failed"
    assert "internal continuation failure" not in completed.result.message
    assert driver.closed_on is not None
    assert service.live_sessions == {}


def test_resume_finalizer_failure_still_closes_session() -> None:
    service, intervention_id, driver, owner_thread = runtime_service()
    del owner_thread
    run_id = str(service.get(intervention_id).intervention.run_id)

    def resume(lease_version: int, outcome: object) -> SuccessResult:
        del lease_version, outcome
        return SuccessResult(
            status="success",
            run_id=run_id,
            capability=CapabilityReference(id="member.lookup", version="1.0.0"),
            outputs={"balance": "1420.57"},
            checkpoint=VerifiedCheckpoint(id="balance_verified", verified=True),
            evidence_manifest=f"evidence://{run_id}/manifest.json",
        )

    def fail_finalization(result: RunResult) -> RunResult:
        del result
        raise OSError("evidence store unavailable")

    service.replay_continuations[intervention_id] = ManagedReplayContinuation(
        lambda: None,
        resume,
    )
    service.replay_result_finalizer = fail_finalization
    claimed = service.claim(intervention_id, 2, "operator-7")

    with pytest.raises(OSError, match="evidence store unavailable"):
        service.begin_resume(intervention_id, claimed.lease.version, "operator-7")

    assert driver.closed_on is not None
    assert service.live_sessions == {}
