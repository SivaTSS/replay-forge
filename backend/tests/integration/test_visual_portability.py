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
ARTIFACT_PATH = REPOSITORY / "capabilities/member.lookup_savings_balance/3.1.0.yaml"


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
            evidence_directory=tmp_path / f"evidence-{tenant}-{viewport.width}",
            demo_base_url=demo_bank,
            browser_viewport_width=viewport.width,
            browser_viewport_height=viewport.height,
        )
    )
    result = runtime.service.invoke(
        "member.lookup_savings_balance",
        "3.1.0",
        tenant,
        {"member_id": member_id},
    )
    return runtime, result


@pytest.mark.parametrize(
    ("tenant", "viewport", "member_id"),
    [
        ("harbor", Viewport(1280, 800), "12345"),
        ("summit", Viewport(1280, 800), "12345"),
        ("harbor", Viewport(1024, 640), "12345"),
        ("summit", Viewport(1440, 900), "12345"),
        ("harbor", Viewport(1280, 800), "13579"),
        ("summit", Viewport(1280, 800), "67890"),
    ],
    ids=[
        "harbor-baseline",
        "summit-baseline",
        "harbor-compact",
        "summit-expanded",
        "delayed-results",
        "known-notice-recovery",
    ],
)
def test_visual_workbench_success_and_portability(
    demo_bank: str, tmp_path: Path, tenant: str, viewport: Viewport, member_id: str
) -> None:
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
            LocalEvidenceStore(tmp_path / f"evidence-{tenant}-{viewport.width}", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        events = runtime.journals[result.run_id].events()
        recovery_events = [event for event in events if event.event_type.startswith("recovery_")]
        if member_id == "67890":
            assert len(recovery_events) == 2
            assert [event.event_type for event in recovery_events] == [
                "recovery_started",
                "recovery_completed",
            ]
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
    ],
    ids=["member-not-found", "permission-denied", "duplicate-search", "changed-icon"],
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
    runtime, result = invoke_visual_workbench(
        demo_bank, tmp_path, tenant, Viewport(1280, 800), member_id
    )
    try:
        assert isinstance(result, BusinessOutcomeResult | FailureResult)
        assert isinstance(result, result_type)
        assert result.code == code
        if isinstance(result, FailureResult):
            assert result.step_id == step_id
        verification = verify_run_manifest(
            LocalEvidenceStore(tmp_path / f"evidence-{tenant}-1280", SystemClock()),
            result.evidence_manifest,
        )
        assert verification.terminal_result_verified
        if isinstance(result, FailureResult):
            manifest = json.loads(
                LocalEvidenceStore(tmp_path / f"evidence-{tenant}-1280", SystemClock()).read(
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
    assert artifact.capability.version == "3.1.0"
    assert all(
        step.target is not None and step.target.visual_candidates and not step.target.candidates
        for step in (*artifact.steps, *(s for r in artifact.recoveries for s in r.steps))
        if step.target is not None
    )
    assert all(
        candidate.strategy != "coordinates"
        for step in (*artifact.steps, *(s for r in artifact.recoveries for s in r.steps))
        if step.target is not None
        for candidate in step.target.candidates
    )
