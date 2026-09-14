"""Real browser/operator discovery handoff with an explicitly injected blocking provider.

This is lifecycle fault injection, not a genuine model-discovery evidence bundle.
"""

import json
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from unittest.mock import Mock, patch

import pytest
import uvicorn
from playwright.sync_api import Route, expect, sync_playwright
from pydantic import SecretStr

from replayforge.api.app import create_app
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.models import (
    CapabilityDraftSpec,
    EscalateProposal,
    PlanningContext,
    ProviderContext,
)
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.vision import RapidOcrTextRecognizer
from tests.integration.test_operator_console import OperatorStack, _free_port

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_operator_console",)
REPOSITORY = Path(__file__).resolve().parents[3]


class BlockingProvider:
    provider_name = "injected-handoff-test"
    model_name = "not-a-discovery"
    calls = 0

    def for_run(self) -> "BlockingProvider":
        return self

    def plan(self, context: PlanningContext) -> CapabilityDraftSpec:
        artifact = load_artifact_yaml(
            (REPOSITORY / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
        )
        return CapabilityDraftSpec(
            operation_slug="injected_handoff_test",
            name="Injected discovery blockage",
            description=context.goal,
            inputs=artifact.inputs,
            outputs=artifact.outputs,
            risk=artifact.capability.risk,
        )

    def decide(self, context: ProviderContext) -> EscalateProposal:
        assert context.screenshot_png.startswith(b"\x89PNG")
        self.calls += 1
        return EscalateProposal(
            kind="escalate", reason_code="injected_blockage", rationale="Explicit lifecycle test"
        )


def test_discovery_operator_resume_repause_terminate_and_shutdown(
    operator_stack: OperatorStack, demo_bank: str, tmp_path: Path
) -> None:
    provider = BlockingProvider()
    telemetry = Mock()
    telemetry.ready.return_value = True
    with (
        patch(
            "replayforge.runtime.composition.OpenAIModelProvider.from_api_key",
            return_value=provider,
        ),
        patch(
            "replayforge.runtime.composition.LangfuseModelCallTelemetry.create",
            return_value=telemetry,
        ),
    ):
        runtime = build_runtime(
            RuntimeSettings(
                artifact_directory=REPOSITORY / "capabilities",
                evidence_directory=tmp_path / "evidence",
                demo_base_url=demo_bank,
                openai_api_key=SecretStr("injected-local-provider"),
                langfuse_public_key=SecretStr("injected-local-metrics"),
                langfuse_secret_key=SecretStr("injected-local-metrics"),
            )
        )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(runtime.api_services), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = monotonic() + 30
    while not server.started:
        assert monotonic() < deadline, "isolated discovery test API failed to start"
        sleep(0.05)

    def forward(route: Route) -> None:
        path = route.request.url.split("/runtime/", 1)[1]
        route.fulfill(response=route.fetch(url=f"http://127.0.0.1:{port}/{path}", timeout=120_000))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            page.route("**/runtime/**", forward)
            page.goto(operator_stack.ui_url)
            page.get_by_role("button", name="Discover new task").click()
            page.get_by_label("Task goal").fill("Resolve a synthetic blocked discovery")
            page.get_by_label("Inputs", exact=False).fill(
                json.dumps({"member_id": "12346", "payoff_date": "2026-09-21"})
            )
            page.get_by_role("button", name="Run and watch", exact=True).click()
            expect(page.get_by_role("button", name="Claim control")).to_be_visible(timeout=90_000)
            first = runtime.intervention_service.list_active()[0]
            run_id = str(first.intervention.run_id)
            session_id = first.intervention.session_id
            assert provider.calls == 1
            page.get_by_role("button", name="Claim control").click()
            page.get_by_role("button", name="Resume automation").click()
            expect(page.get_by_text("Resume was not safe:", exact=False)).to_be_visible()
            assert provider.calls == 1
            page.get_by_role("button", name="Claim control").click()
            viewport = page.get_by_alt_text(
                "Current retained browser viewport; click to send a left-click"
            )
            expect(viewport).to_be_visible()
            frame = bytes(
                viewport.evaluate(
                    "async image => Array.from(new Uint8Array("
                    "await (await fetch(image.src)).arrayBuffer()))"
                )
            )
            labels = [
                token
                for token in RapidOcrTextRecognizer().recognize(frame)
                if "Member ID / name / city" in token.text
            ]
            assert len(labels) == 1
            bounds = labels[0].region
            # A human clicks the visible form caption; no saved coordinates or DOM target.
            box = viewport.bounding_box()
            assert box is not None
            dimensions = viewport.evaluate("image => [image.naturalWidth, image.naturalHeight]")
            viewport.click(
                position={
                    "x": (bounds.x + bounds.width / 2) * box["width"] / dimensions[0],
                    "y": (bounds.y + bounds.height / 2) * box["height"] / dimensions[1],
                }
            )
            page.get_by_label("Text (not retained)").fill("12346")
            page.get_by_role("button", name="Send", exact=True).click()
            with page.expect_response("**/interventions/*/resume") as resume_response:
                page.get_by_role("button", name="Resume automation").click()
            resumed = resume_response.value.json()
            assert resumed["status"] == "resolved", resumed
            deadline = monotonic() + 60
            while True:
                active = runtime.intervention_service.list_active()
                if (
                    active
                    and active[0].intervention.id != first.intervention.id
                    and str(active[0].intervention.id) in runtime.live_sessions
                ):
                    second = active[0]
                    break
                assert monotonic() < deadline, "resumed model did not route its second blocker"
                page.wait_for_timeout(100)
            expect(page.get_by_role("button", name="Claim control")).to_be_visible(timeout=90_000)
            assert second.intervention.id != first.intervention.id
            assert second.intervention.session_id == session_id
            assert provider.calls == 2
            runtime.intervention_service.terminate(
                str(second.intervention.id), second.lease.version, None, "Explicit test termination"
            )
            controller = runtime.execution_controller
            assert controller is not None
            deadline = monotonic() + 15
            while not runtime.journals[run_id]._finalized:
                assert monotonic() < deadline
                sleep(0.05)
            events = {event.event_type for event in runtime.journals[run_id].events()}
            assert {
                "human_input_applied",
                "automation_resumed",
                "resume_checkpoint_verified",
                "discovery_failed",
            } <= events
            assert "artifact_compiled" not in events
            verified = verify_run_manifest(
                LocalEvidenceStore(tmp_path / "evidence", SystemClock()),
                runtime.journals[run_id].evidence_manifest_key,
            )
            assert verified.attachment_count >= 3
            assert not runtime.live_sessions
            assert not runtime.intervention_service.discovery_waits
            expect(page.locator(".execution-panel > .panel-heading > .status")).to_have_text(
                "terminated"
            )
            # A second blocked execution must not deadlock runtime shutdown.
            page.get_by_role("button", name="Run and watch", exact=True).click()
            expect(page.get_by_role("button", name="Claim control")).to_be_visible(timeout=90_000)
            runtime.close()
            assert not runtime.live_sessions
            assert not runtime.intervention_service.discovery_waits
            browser.close()
    finally:
        runtime.close()
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
