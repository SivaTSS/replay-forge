"""Optional DOM adapter and same-session handoff; no synthetic discovery claims."""

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
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.interventions.models import HumanInputCommand
from replayforge.policy.types import Risk
from replayforge.runs.results import FailureResult, InterventionRequiredResult, SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.models import HumanPointerInput
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
        result = runtime.service.invoke(
            "member.servicing_loan_payoff_quote",
            "1.0.1",
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
