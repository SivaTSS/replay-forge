from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from replayforge.capabilities.models import (
    ClickAction,
    FrameLocator,
    IdentityMatchesCondition,
    InputValue,
    LiteralValue,
    LocatorBundle,
    LocatorCandidate,
    LocatorScope,
    LocatorStrategy,
    MatchMode,
    OutputValidCondition,
    RouteCondition,
    SelectAction,
    TextCondition,
    TypeAction,
)
from replayforge.capabilities.serialization import (
    artifact_content_hash,
    dump_artifact_yaml,
    load_artifact_yaml,
)
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.interventions.leases import (
    ControlLeaseService,
    InMemoryControlLeaseRepository,
)
from replayforge.interventions.models import HumanInputCommand
from replayforge.interventions.router import InMemoryInterventionRouter
from replayforge.interventions.service import InterventionCoordinator
from replayforge.policy.types import Risk
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.runs.results import (
    BusinessOutcomeResult,
    FailureResult,
    InterventionRequiredResult,
    SuccessResult,
)
from replayforge.runtime.composition import (
    LiveBrowserSession,
    RuntimeInterventionService,
    build_runtime,
)
from replayforge.runtime.settings import RuntimeSettings
from replayforge.runtime.worker import SerialSessionWorker
from replayforge.shared.clock import SystemClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import (
    HumanKey,
    HumanKeyInput,
    HumanPointerInput,
    HumanTextInput,
    Viewport,
)
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def demo_bank() -> Iterator[str]:
    repository = Path(__file__).resolve().parents[3]
    app = repository / "apps" / "demo-bank"
    process = subprocess.Popen(
        [
            str(app / "node_modules" / ".bin" / "next"),
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            "3001",
        ],
        cwd=app,
        env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base_url = "http://127.0.0.1:3001"
    try:
        for _ in range(50):
            try:
                with urlopen(f"{base_url}/harbor", timeout=1) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("demo bank did not become ready")
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)


def in_member_frame(target: LocatorBundle) -> LocatorBundle:
    return target.model_copy(
        update={
            "scope": LocatorScope(
                frame_path=(
                    FrameLocator(
                        locator=LocatorCandidate(
                            strategy=LocatorStrategy.TITLE, value="Member operations"
                        )
                    ),
                )
            )
        }
    )


def test_real_iframe_search_and_account_extraction(demo_bank: str, tmp_path: Path) -> None:
    driver = PlaywrightSurfaceDriver(demo_bank)
    session = driver.open("northstar_member_service", "harbor", "member_search")
    try:
        assert session.observe().route == "/members/search"
        assert session.capture_provider_frame().startswith(b"\x89PNG\r\n\x1a\n")
        sanitized_frame = session.capture_sanitized_evidence_frame()
        assert sanitized_frame.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert sanitized_frame.redaction_directives == (
            "mask:form-controls",
            "mask:customer-details",
            "mask:account-table-cells",
        )
        frame = driver.capture_active_frame()
        assert frame.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert frame.viewport == Viewport(1280, 800)
        member_field = in_member_frame(
            LocatorBundle(
                description="Member ID field",
                candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Member ID"),),
            )
        )
        member_target = session.resolve(member_field, 5_000)
        assert member_target.registered_risk is Risk.READ_ONLY
        session.execute(
            TypeAction(kind="type", value=InputValue(source="input", path="member_id")),
            member_target,
            {"member_id": "12345"},
        )
        search = in_member_frame(
            LocatorBundle(
                description="Search button",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.ROLE_NAME,
                        role="button",
                        name="Search",
                    ),
                ),
            )
        )
        search_target = session.resolve(search, 5_000)
        assert search_target.registered_risk is Risk.READ_ONLY
        session.execute(ClickAction(kind="click"), search_target, {})
        details = in_member_frame(
            LocatorBundle(
                description="Savings details link",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.ROLE_NAME,
                        role="link",
                        name="View details",
                    ),
                ),
            )
        )
        details_target = session.resolve(details, 5_000)
        assert details_target.registered_risk is Risk.READ_ONLY
        session.execute(ClickAction(kind="click"), details_target, {})
        assert session.wait_until(
            RouteCondition(kind="route", pattern="/accounts/*/details"), {}, {}, 5_000
        )
        balance = in_member_frame(
            LocatorBundle(
                description="Available balance",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.RELATIVE_TEXT,
                        anchor="Available balance",
                        relation="following_value",
                        element="dd",
                    ),
                ),
            )
        )
        assert session.extract(session.resolve(balance, 5_000)) == "$1,420.57"
        outputs = {"member_id": "12345"}
        inputs = {"member_id": "12345"}
        assert session.evaluate(
            OutputValidCondition(kind="output_valid", output="member_id"), outputs, inputs
        )
        assert session.evaluate(
            IdentityMatchesCondition(
                kind="identity_matches",
                extracted_output="member_id",
                input_path="member_id",
            ),
            outputs,
            inputs,
        )
        screenshot = tmp_path / "account-details.png"
        session.screenshot(screenshot)
        assert screenshot.stat().st_size > 1_000
    finally:
        session.close()
        driver.close()


def test_real_iframe_selects_known_runtime_scenario(demo_bank: str) -> None:
    driver = PlaywrightSurfaceDriver(demo_bank)
    session = driver.open("northstar_member_service", "harbor", "member_search")
    try:
        scenario = in_member_frame(
            LocatorBundle(
                description="Runtime scenario selector",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.LABEL,
                        value="Runtime scenario",
                    ),
                ),
            )
        )
        session.execute(
            SelectAction(
                kind="select",
                option=LiteralValue(source="literal", value="Known interstitial"),
            ),
            session.resolve(scenario, 5_000),
            {},
        )
        member_field = in_member_frame(
            LocatorBundle(
                description="Member ID field",
                candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Member ID"),),
            )
        )
        session.execute(
            TypeAction(kind="type", value=InputValue(source="input", path="member_id")),
            session.resolve(member_field, 5_000),
            {"member_id": "12345"},
        )
        search = in_member_frame(
            LocatorBundle(
                description="Search button",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.ROLE_NAME,
                        role="button",
                        name="Search",
                    ),
                ),
            )
        )
        session.execute(ClickAction(kind="click"), session.resolve(search, 5_000), {})

        assert session.wait_until(
            TextCondition(kind="text", value="Important notice", match=MatchMode.EXACT),
            {},
            {},
            5_000,
        )
    finally:
        session.close()
        driver.close()


def test_registered_artifact_replays_end_to_end(demo_bank: str, tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=repository / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
        )
    )
    try:
        result = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.0",
            "harbor",
            {"member_id": "12345"},
        )

        assert isinstance(result, SuccessResult)
        assert result.outputs == {
            "member_id": "12345",
            "account_type": "savings",
            "currency": "USD",
            "available_balance": "1420.57",
            "as_of": "2026-09-10T12:30:00Z",
        }
        assert result.checkpoint.verified
        event_types = [event.event_type for event in runtime.journals[result.run_id].events()]
        assert event_types[0] == "replay_started"
        assert event_types[-1] == "checkpoint_verified"
        manifest_path = tmp_path / "evidence" / result.evidence_manifest.removeprefix("evidence://")
        manifest = json.loads(manifest_path.read_text())
        assert manifest["run_id"] == result.run_id
        assert len(manifest["events"]) == len(event_types)
        terminal_path = (
            tmp_path / "evidence" / manifest["terminal_result"]["key"].removeprefix("evidence://")
        )
        terminal = json.loads(terminal_path.read_text())
        assert terminal["status"] == "success"
        assert terminal["outputs"]["member_id"].startswith("customer_")
        assert {key: value for key, value in terminal["outputs"].items() if key != "member_id"} == {
            "account_type": "[REDACTED_FINANCIAL]",
            "as_of": "2026-09-10T12:30:00Z",
            "available_balance": "[REDACTED_FINANCIAL]",
            "currency": "[REDACTED_FINANCIAL]",
        }
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        assert runtime.live_sessions == {}
    finally:
        runtime.close()


def test_registered_artifact_recovers_from_known_interstitial(
    demo_bank: str, tmp_path: Path
) -> None:
    repository = Path(__file__).resolve().parents[3]
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=repository / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
        )
    )
    try:
        result = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.1",
            "harbor",
            {"member_id": "12345"},
        )

        assert isinstance(result, SuccessResult)
        assert result.outputs["available_balance"] == "1420.57"
        assert result.checkpoint.verified
        events = runtime.journals[result.run_id].events()
        recovery_events = [event for event in events if event.event_type.startswith("recovery_")]
        assert [event.event_type for event in recovery_events] == [
            "recovery_started",
            "recovery_completed",
        ]
        assert recovery_events[0].step_id == "search.submit"
        assert recovery_events[0].details == {
            "recovery_id": "dismiss_known_notice",
            "use": 1,
        }
        assert recovery_events[1].details == {
            "recovery_id": "dismiss_known_notice",
            "resume_at": "account.open_savings",
        }
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        assert runtime.live_sessions == {}
    finally:
        runtime.close()


def test_registered_artifact_returns_real_member_not_found_outcome(
    demo_bank: str, tmp_path: Path
) -> None:
    repository = Path(__file__).resolve().parents[3]
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=repository / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
        )
    )
    try:
        result = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.0",
            "harbor",
            {"member_id": "99999"},
        )

        assert isinstance(result, BusinessOutcomeResult)
        assert result.code == "member_not_found"
        assert result.details == {"member_id": "***9999"}
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
    finally:
        runtime.close()


def test_real_output_failure_retains_masked_state_before_teardown(
    demo_bank: str, tmp_path: Path
) -> None:
    repository = Path(__file__).resolve().parents[3]
    artifact = load_artifact_yaml(
        (repository / "capabilities/member.lookup_savings_balance/1.0.0.yaml").read_text()
    )
    properties = dict(artifact.outputs.properties)
    properties["available_balance"] = properties["available_balance"].model_copy(
        update={"pattern": "^999\\.99$"}
    )
    modified = artifact.model_copy(
        update={
            "outputs": artifact.outputs.model_copy(update={"properties": properties}),
            "provenance": artifact.provenance.model_copy(update={"artifact_content_hash": None}),
        }
    )
    modified = modified.model_copy(
        update={
            "provenance": modified.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(modified)}
            )
        }
    )
    artifact_path = tmp_path / "capabilities/member.lookup_savings_balance/1.0.0.yaml"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(dump_artifact_yaml(modified))
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=tmp_path / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
        )
    )
    try:
        result = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.0",
            "harbor",
            {"member_id": "12345"},
        )

        assert isinstance(result, FailureResult)
        assert result.code == "output_validation_failed"
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.attachment_count == 1
        manifest_path = tmp_path / "evidence" / result.evidence_manifest.removeprefix("evidence://")
        manifest = json.loads(manifest_path.read_text())
        attachment = manifest["attachments"][0]
        assert attachment["retention_class"] == "failure"
        assert attachment["redaction_directives"] == [
            "mask:form-controls",
            "mask:customer-details",
            "mask:account-table-cells",
        ]
        screenshot = (
            tmp_path / "evidence" / attachment["key"].removeprefix("evidence://")
        ).read_bytes()
        assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        assert runtime.live_sessions == {}
    finally:
        runtime.close()


def test_human_input_controls_original_browser_session(demo_bank: str) -> None:
    clock = SystemClock()
    driver = PlaywrightSurfaceDriver(demo_bank)
    worker = SerialSessionWorker("handoff-integration")
    session = worker.call(
        lambda: driver.open("northstar_member_service", "harbor", "member_search")
    )
    leases = ControlLeaseService(InMemoryControlLeaseRepository(), clock)
    router = InMemoryInterventionRouter(clock)
    run_id = str(new_id(EntityKind.RUN))
    intervention_id = str(new_id(EntityKind.INTERVENTION))
    initial = leases.create_for_automation(str(session.session_id))
    paused = leases.pause(str(session.session_id), initial.version, intervention_id)
    observation = worker.call(session.observe)
    router.create(
        intervention_id=intervention_id,
        run_id=run_id,
        session_id=str(session.session_id),
        code="unexpected_dialog",
        step_id="search.member_id",
        observation=observation,
    )
    journal = InMemoryRunJournal(run_id, clock)
    service = RuntimeInterventionService(
        InterventionCoordinator(router, leases),
        {intervention_id: LiveBrowserSession(worker, driver)},
        {run_id: journal},
        Lock(),
    )
    claimed = service.claim(intervention_id, paused.version, "operator-7")

    try:
        member_field = worker.call(
            lambda: session.page.frame_locator('iframe[title="Member operations"]')
            .get_by_label("Member ID", exact=True)
            .bounding_box()
        )
        assert member_field is not None
        frame = service.viewport(intervention_id, claimed.lease.version, "operator-7")
        service.send_input(
            intervention_id,
            claimed.lease.version,
            "operator-7",
            HumanInputCommand(
                client_sequence=frame.next_client_sequence,
                source_frame_sequence=frame.sequence,
                viewport=frame.viewport,
                action=HumanPointerInput(
                    int(member_field["x"] + member_field["width"] / 2),
                    int(member_field["y"] + member_field["height"] / 2),
                ),
            ),
        )
        frame = service.viewport(intervention_id, claimed.lease.version, "operator-7")
        service.send_input(
            intervention_id,
            claimed.lease.version,
            "operator-7",
            HumanInputCommand(
                client_sequence=frame.next_client_sequence,
                source_frame_sequence=frame.sequence,
                viewport=frame.viewport,
                action=HumanTextInput("67890"),
            ),
        )

        assert (
            worker.call(
                lambda: session.page.frame_locator('iframe[title="Member operations"]')
                .get_by_label("Member ID", exact=True)
                .input_value()
            )
            == "67890"
        )
        assert driver.active_session is session
        assert str(driver.active_session.session_id) == str(session.session_id)
        events = journal.events()
        assert [event.event_type for event in events] == [
            "human_input_dispatched",
            "human_input_applied",
            "human_input_dispatched",
            "human_input_applied",
        ]
        assert "67890" not in repr(events)
    finally:
        service.terminate(
            intervention_id,
            service.get(intervention_id).lease.version,
            "operator-7",
            "Integration test complete.",
        )


def test_replay_resumes_after_validated_same_session_handoff(
    demo_bank: str, tmp_path: Path
) -> None:
    repository = Path(__file__).resolve().parents[3]
    artifact = load_artifact_yaml(
        (repository / "capabilities/member.lookup_savings_balance/1.0.0.yaml").read_text()
    )
    steps = list(artifact.steps)
    steps[1] = steps[1].model_copy(update={"risk": Risk.SENSITIVE})
    modified = artifact.model_copy(
        update={
            "capability": artifact.capability.model_copy(update={"risk": Risk.SENSITIVE}),
            "steps": tuple(steps),
            "policy": artifact.policy.model_copy(update={"maximum_risk": Risk.SENSITIVE}),
            "provenance": artifact.provenance.model_copy(update={"artifact_content_hash": None}),
        }
    )
    modified = modified.model_copy(
        update={
            "provenance": modified.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(modified)}
            )
        }
    )
    artifact_path = tmp_path / "capabilities/member.lookup_savings_balance/1.0.0.yaml"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(dump_artifact_yaml(modified))
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=tmp_path / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
        )
    )

    try:
        paused = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.0",
            "harbor",
            {"member_id": "12345"},
        )
        assert isinstance(paused, InterventionRequiredResult)
        open_transition = runtime.intervention_service.get(paused.intervention_id)
        claimed = runtime.intervention_service.claim(
            paused.intervention_id, open_transition.lease.version, "operator-7"
        )
        frame = runtime.intervention_service.viewport(
            paused.intervention_id, claimed.lease.version, "operator-7"
        )
        runtime.intervention_service.send_input(
            paused.intervention_id,
            claimed.lease.version,
            "operator-7",
            HumanInputCommand(
                client_sequence=frame.next_client_sequence,
                source_frame_sequence=frame.sequence,
                viewport=frame.viewport,
                action=HumanKeyInput(HumanKey.ENTER),
            ),
        )

        completed = runtime.intervention_service.begin_resume(
            paused.intervention_id, claimed.lease.version, "operator-7"
        )

        assert isinstance(completed.result, SuccessResult)
        assert completed.result.outputs["member_id"] == "12345"
        assert completed.result.outputs["available_balance"] == "1420.57"
        assert completed.result.checkpoint.verified
        assert completed.transition.intervention.status.value == "resolved"
        event_types = [event.event_type for event in runtime.journals[paused.run_id].events()]
        assert "human_input_applied" in event_types
        assert "resume_checkpoint_verified" in event_types
        assert "automation_resumed" in event_types
        assert runtime.live_sessions == {}
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            completed.result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        assert verification.attachment_count == 2
        manifest_path = (
            tmp_path / "evidence" / completed.result.evidence_manifest.removeprefix("evidence://")
        )
        manifest = json.loads(manifest_path.read_text())
        assert [entry["retention_class"] for entry in manifest["attachments"]] == [
            "human_audit",
            "human_audit",
        ]
        assert all(
            entry["redaction_directives"]
            == [
                "mask:form-controls",
                "mask:customer-details",
                "mask:account-table-cells",
            ]
            for entry in manifest["attachments"]
        )
    finally:
        runtime.close()
