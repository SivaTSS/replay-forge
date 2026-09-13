from __future__ import annotations

import json
from pathlib import Path

import pytest

from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.runs.results import BusinessOutcomeResult, FailureResult, RunResult, SuccessResult
from replayforge.runtime.composition import LocalRuntime, build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.surfaces.models import Viewport
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver

pytestmark = pytest.mark.integration
REPOSITORY = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = REPOSITORY / "capabilities/member.lookup_savings_balance/3.2.0.yaml"


def evidence_path(root: Path, tenant: str, viewport: Viewport) -> Path:
    scale = str(viewport.device_scale).replace(".", "_")
    return root / f"evidence-{tenant}-{viewport.width}x{viewport.height}@{scale}x"


def invoke_visual_workbench(
    demo_bank: str,
    tmp_path: Path,
    tenant: str,
    viewport: Viewport,
    member_id: str,
) -> tuple[LocalRuntime, RunResult]:
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=REPOSITORY / "capabilities",
            capability_asset_directory=REPOSITORY / "capabilities/_assets",
            evidence_directory=evidence_path(tmp_path, tenant, viewport),
            demo_base_url=demo_bank,
            browser_viewport_width=viewport.width,
            browser_viewport_height=viewport.height,
            browser_device_scale_factor=viewport.device_scale,
        )
    )
    result = runtime.service.invoke(
        "member.lookup_savings_balance",
        "3.2.0",
        tenant,
        {"member_id": member_id},
    )
    return runtime, result


@pytest.mark.parametrize(
    ("tenant", "viewport"),
    [
        ("harbor", Viewport(800, 600, 1.0)),
        ("summit", Viewport(900, 700, 2.0)),
        ("harbor", Viewport(1024, 768, 1.25)),
        ("summit", Viewport(1280, 720, 1.0)),
        ("harbor", Viewport(1440, 900, 1.5)),
        ("summit", Viewport(1920, 1080, 2.0)),
    ],
    ids=[
        "harbor-800x600@1x",
        "summit-900x700@2x",
        "harbor-1024x768@1_25x",
        "summit-1280x720@1x",
        "harbor-1440x900@1_5x",
        "summit-1920x1080@2x",
    ],
)
def test_visual_workbench_success_and_portability(
    demo_bank: str, tmp_path: Path, tenant: str, viewport: Viewport
) -> None:
    member_id = "12345"
    runtime, result = invoke_visual_workbench(demo_bank, tmp_path, tenant, viewport, member_id)
    try:
        assert isinstance(result, SuccessResult)
        assert result.outputs == {
            "member_id": member_id,
            "account_type": "savings",
            "currency": "USD",
            "available_balance": "1420.57",
            "as_of": "2026-09-10T12:30:00Z",
        }
        verification = verify_run_manifest(
            LocalEvidenceStore(evidence_path(tmp_path, tenant, viewport), SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        assert result.evidence_manifest
        events = runtime.journals[result.run_id].events()
        recovery_events = [event for event in events if event.event_type.startswith("recovery_")]
        assert recovery_events == []
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "member_id", ["13579", "67890"], ids=["delayed-results", "known-notice-recovery"]
)
def test_visual_workbench_handles_declared_recovery(
    demo_bank: str, tmp_path: Path, member_id: str
) -> None:
    viewport = Viewport(1280, 800, 1.0)
    runtime, result = invoke_visual_workbench(demo_bank, tmp_path, "harbor", viewport, member_id)
    try:
        assert isinstance(result, SuccessResult)
        assert result.outputs["available_balance"] == "1420.57"
        recovery_events = [
            event.event_type
            for event in runtime.journals[result.run_id].events()
            if event.event_type.startswith("recovery_")
        ]
        if member_id == "67890":
            assert recovery_events == ["recovery_started", "recovery_completed"]
        else:
            assert recovery_events == []
    finally:
        runtime.close()


@pytest.mark.parametrize(
    ("tenant", "member_id", "result_type", "code", "step_id"),
    [
        ("harbor", "99999", BusinessOutcomeResult, "member_not_found", None),
        ("summit", "24680", FailureResult, "permission_denied", "search.submit"),
        ("harbor", "33333", FailureResult, "target_ambiguous", "search.submit"),
        ("summit", "44444", FailureResult, "target_absent", "account.open_savings"),
        ("harbor", "55555", FailureResult, "target_ambiguous", "account.extract_available_balance"),
    ],
    ids=[
        "member-not-found",
        "permission-denied",
        "duplicate-search",
        "changed-icon",
        "duplicate-field",
    ],
)
def test_visual_workbench_reports_declared_and_fail_closed_states(
    demo_bank: str,
    tmp_path: Path,
    tenant: str,
    member_id: str,
    result_type: type[BusinessOutcomeResult | FailureResult],
    code: str,
    step_id: str | None,
) -> None:
    viewport = Viewport(1280, 800, 1.0)
    runtime, result = invoke_visual_workbench(demo_bank, tmp_path, tenant, viewport, member_id)
    try:
        assert isinstance(result, BusinessOutcomeResult | FailureResult)
        assert isinstance(result, result_type)
        assert result.code == code
        if isinstance(result, FailureResult):
            assert result.step_id == step_id
        verification = verify_run_manifest(
            LocalEvidenceStore(evidence_path(tmp_path, tenant, viewport), SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        if isinstance(result, FailureResult):
            manifest = json.loads(
                LocalEvidenceStore(evidence_path(tmp_path, tenant, viewport), SystemClock()).read(
                    result.evidence_manifest
                )
            )
            failure_frames = [
                attachment
                for attachment in manifest["attachments"]
                if attachment["media_type"] == "image/png"
            ]
            assert failure_frames
            assert all(
                "mask:rendered-canvas" in frame["redaction_directives"] for frame in failure_frames
            )
        if member_id == "33333":
            events = runtime.journals[result.run_id].events()
            assert not any(
                event.event_type == "action_intent" and event.step_id == "search.submit"
                for event in events
            )
    finally:
        runtime.close()


def test_visual_workbench_exposes_only_a_canvas(demo_bank: str) -> None:
    driver = PlaywrightSurfaceDriver(demo_bank)
    session = driver.open("northstar_member_service", "harbor", "visual_member_workbench")
    try:
        observation = session.observe()
        assert observation.route == "/members/search"
        assert observation.actionable_controls == ()
        assert observation.extractable_fields == ()
        assert session.page.locator("canvas").count() == 1
    finally:
        session.close()
        driver.close()


def test_visual_workbench_artifact_has_no_semantic_or_coordinate_targets() -> None:
    artifact = load_artifact_yaml(ARTIFACT_PATH.read_text())
    assert artifact.capability.version == "3.2.0"
    assert all(
        step.target is not None and step.target.visual_candidates and not step.target.candidates
        for step in (*artifact.steps, *(s for r in artifact.recoveries for s in r.steps))
        if step.target is not None
    )
    assert all(
        candidate.strategy
        in {
            "rendered_text",
            "rendered_labeled_control",
            "rendered_field_value",
            "rendered_group_image",
        }
        for step in (*artifact.steps, *(s for r in artifact.recoveries for s in r.steps))
        if step.target is not None
        for candidate in step.target.visual_candidates
    )
