"""Optional DOM adapter and same-session handoff; no synthetic discovery claims."""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from playwright.sync_api import sync_playwright

from replayforge.capabilities.models import (
    FrameLocator,
    InputValue,
    LocatorBundle,
    LocatorCandidate,
    LocatorScope,
    LocatorStrategy,
    RenderedTextCondition,
    TypeAction,
)
from replayforge.capabilities.serialization import (
    artifact_content_hash,
    dump_artifact_yaml,
    load_artifact_yaml,
)
from replayforge.evidence.export import EvidenceExportRequest, export_evidence_bundle
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.interventions.models import HumanInputCommand
from replayforge.policy.types import Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult, SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.models import HumanPointerInput, ResolvedTarget
from replayforge.surfaces.playwright import PlaywrightSurfaceSession
from replayforge.surfaces.vision import RapidOcrTextRecognizer

pytestmark = pytest.mark.integration
REPOSITORY = Path(__file__).resolve().parents[3]


def test_generic_dom_frame_adapter_and_private_screenshot_retention() -> None:
    """In-memory HTML tests the optional adapter, not a second demo application."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_content(
            '<iframe title="Inventory panel" srcdoc="&lt;label&gt;Reference'
            '&lt;input&gt;&lt;/label&gt;"></iframe>'
        )
        session = PlaywrightSurfaceSession(context, page, "inventory", "test", {})
        try:
            bundle = LocatorBundle(
                description="Reference input",
                scope=LocatorScope(
                    frame_path=(
                        FrameLocator(
                            locator=LocatorCandidate(
                                strategy=LocatorStrategy.TITLE, value="Inventory panel"
                            )
                        ),
                    )
                ),
                candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Reference"),),
            )
            session.execute(
                TypeAction(kind="type", value=InputValue(source="input", path="reference")),
                session.resolve(bundle, 2_000),
                {"reference": "private-test-reference"},
            )
            field = page.frame_locator("iframe").get_by_label("Reference")
            assert field.input_value() == "private-test-reference"
            retained = session.capture_sanitized_evidence_frame()
            pixels = cv2.imdecode(np.frombuffer(retained.content, np.uint8), cv2.IMREAD_COLOR)
            assert pixels is not None and np.all(pixels == (39, 24, 17))
            assert retained.redaction_directives == ("mask:full-viewport",)
        finally:
            session.close()
            browser.close()


def handoff_fixture(tmp_path: Path) -> Path:
    """Inject a policy boundary into a copy, never the published discovery trace."""
    source = REPOSITORY / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml"
    artifact = load_artifact_yaml(source.read_text())
    steps = list(artifact.steps)
    steps[2] = steps[2].model_copy(
        update={
            "risk": Risk.SENSITIVE,
            "postconditions": (
                RenderedTextCondition(kind="rendered_text", value="Account relationships"),
            ),
        }
    )
    fixture = artifact.model_copy(
        update={
            "steps": tuple(steps),
            "capability": artifact.capability.model_copy(update={"risk": Risk.SENSITIVE}),
            "policy": artifact.policy.model_copy(update={"maximum_risk": Risk.SENSITIVE}),
            "provenance": artifact.provenance.model_copy(
                update={
                    "provider": "injected-handoff-test",
                    "model": "not-a-discovery",
                    "artifact_content_hash": None,
                }
            ),
        }
    )
    fixture = fixture.model_copy(
        update={
            "provenance": fixture.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(fixture)}
            )
        }
    )
    root = tmp_path / "capabilities"
    path = root / fixture.capability.id / f"{fixture.capability.version}.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(dump_artifact_yaml(fixture))
    return root


def test_visual_obstruction_handoff_retries_original_step(
    demo_bank: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test-injected screen obstruction; the published program is unchanged, no model calls."""
    original = PlaywrightSurfaceSession.resolve
    obstructed: set[str] = set()

    def resolve(
        session: PlaywrightSurfaceSession, target: LocatorBundle, timeout_ms: int
    ) -> ResolvedTarget:
        if not obstructed:
            obstructed.add(str(session.session_id))
            session.page.evaluate("""() => {
                const overlay = document.createElement('div');
                overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:white;'
                    + 'display:grid;place-items:center';
                const dismiss = document.createElement('button');
                dismiss.textContent = 'Dismiss obstruction';
                dismiss.style.cssText = 'font:28px Arial;padding:24px;color:black;background:white';
                dismiss.onclick = () => overlay.remove();
                overlay.append(dismiss); document.body.append(overlay);
            }""")
        return original(session, target, timeout_ms)

    monkeypatch.setattr(PlaywrightSurfaceSession, "resolve", resolve)
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=REPOSITORY / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    artifact = load_artifact_yaml(
        (REPOSITORY / "capabilities/member.servicing_loan_payoff_quote/1.0.2.yaml").read_text()
    )
    export_root = os.getenv("REPLAYFORGE_HANDOFF_EVIDENCE_ROOT")
    export_commit = os.getenv("REPLAYFORGE_HANDOFF_EVIDENCE_COMMIT")
    if export_root:
        assert export_commit, "Evidence export requires the actual tested commit."
        assert not (Path(export_root) / "replay-injected-obstruction-handoff").exists()
    try:
        paused = runtime.service.invoke(
            artifact.capability.id,
            artifact.capability.version,
            "harbor",
            {"member_id": "12345", "payoff_date": "2026-09-20"},
        )
        assert isinstance(paused, InterventionRequiredResult), paused
        assert paused.code == "target_absent" and paused.step_id == artifact.steps[0].id
        journal = runtime.journals[paused.run_id]
        assert not any(event.event_type == "action_intent" for event in journal.events())
        service = runtime.intervention_service
        opened = service.get(paused.intervention_id)
        assert str(opened.intervention.session_id) in obstructed
        claimed = service.claim(paused.intervention_id, opened.lease.version, "operator-test")
        frame = service.viewport(paused.intervention_id, claimed.lease.version, "operator-test")
        matches = [
            token
            for token in RapidOcrTextRecognizer().recognize(frame.content)
            if token.text.strip() == "Dismiss obstruction"
        ]
        assert len(matches) == 1
        region = matches[0].region
        service.send_input(
            paused.intervention_id,
            claimed.lease.version,
            "operator-test",
            HumanInputCommand(
                client_sequence=frame.next_client_sequence,
                source_frame_sequence=frame.sequence,
                viewport=frame.viewport,
                action=HumanPointerInput(
                    region.x + region.width // 2, region.y + region.height // 2
                ),
            ),
        )
        completed = service.begin_resume(
            paused.intervention_id, claimed.lease.version, "operator-test"
        )
        assert isinstance(completed.result, SuccessResult), completed.result
        assert completed.result.outputs == {
            "payoff_amount": "$7,832.25",
            "good_through_date": "2026-09-20",
            "confirmation_reference": "HBR-000001",
        }
        assert completed.transition.intervention.session_id == opened.intervention.session_id
        events = journal.events()
        assert (
            sum(
                event.event_type == "action_intent" and event.step_id == artifact.steps[0].id
                for event in events
            )
            == 1
        )
        assert any(
            event.event_type == "resume_checkpoint_verified"
            and event.details.get("disposition") == "retry_step"
            for event in events
        )
        assert not any(event.event_type.startswith("model_") for event in events)
        verified = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            completed.result.evidence_manifest,
        )
        assert verified.terminal_result_verified and verified.attachment_count == 3
        assert runtime.live_sessions == {}
        if export_root and export_commit:
            export_evidence_bundle(
                LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
                Path(export_root) / "replay-injected-obstruction-handoff",
                EvidenceExportRequest(
                    scenario="replay-injected-obstruction-handoff",
                    artifact=artifact,
                    source_manifest_key=completed.result.evidence_manifest,
                    commands=(
                        "uv run pytest backend/tests/integration/test_playwright_surface.py::"
                        "test_visual_obstruction_handoff_retries_original_step -q",
                    ),
                    commit_sha=export_commit,
                ),
            )
    finally:
        runtime.close()


def test_missing_record_fails_closed_with_masked_evidence(demo_bank: str, tmp_path: Path) -> None:
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=REPOSITORY / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    try:
        artifact = load_artifact_yaml(
            (REPOSITORY / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
        )
        result = runtime.service.validate_artifact(
            artifact,
            "harbor",
            {"member_id": "00000", "payoff_date": "2026-09-20"},
        )
        assert isinstance(result, FailureResult), result
        assert result.code == "target_absent"
        artifact = load_artifact_yaml(
            (REPOSITORY / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
        )
        assert result.step_id == artifact.steps[2].id
        verified = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()), result.evidence_manifest
        )
        assert verified.terminal_result_verified
        assert verified.attachment_count >= 1
        assert runtime.live_sessions == {}
    finally:
        runtime.close()


def test_replay_resumes_after_same_session_handoff_on_workstation(
    demo_bank: str, tmp_path: Path
) -> None:
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=handoff_fixture(tmp_path),
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    try:
        paused = runtime.service.invoke(
            "member.servicing_loan_payoff_quote",
            "1.0.1",
            "harbor",
            {"member_id": "12345", "payoff_date": "2026-09-20"},
        )
        assert isinstance(paused, InterventionRequiredResult), paused
        service = runtime.intervention_service
        opened = service.get(paused.intervention_id)
        session_id = opened.intervention.session_id
        claimed = service.claim(paused.intervention_id, opened.lease.version, "operator-test")
        frame = service.viewport(paused.intervention_id, claimed.lease.version, "operator-test")
        # Simulate a human selecting the sole Open action in the filtered list.
        tokens = RapidOcrTextRecognizer().recognize(frame.content)
        matches = [token for token in tokens if token.text.strip() == "Open"]
        assert len(matches) == 1
        region = matches[0].region
        service.send_input(
            paused.intervention_id,
            claimed.lease.version,
            "operator-test",
            HumanInputCommand(
                client_sequence=frame.next_client_sequence,
                source_frame_sequence=frame.sequence,
                viewport=frame.viewport,
                action=HumanPointerInput(
                    region.x + region.width // 2, region.y + region.height // 2
                ),
            ),
        )
        completed = service.begin_resume(
            paused.intervention_id, claimed.lease.version, "operator-test"
        )
        assert isinstance(completed.result, SuccessResult), completed.result
        assert completed.result.outputs["payoff_amount"] == "$7,832.25"
        assert completed.transition.intervention.session_id == session_id
        events = runtime.journals[paused.run_id].events()
        expected = {"human_input_applied", "resume_checkpoint_verified", "automation_resumed"}
        assert expected <= {event.event_type for event in events}
        assert runtime.live_sessions == {}
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
            completed.result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        assert verification.attachment_count == 2
    finally:
        runtime.close()
