"""Verify restoration boundaries; loading evidence does not run or simulate discovery."""

from dataclasses import replace
from pathlib import Path
from shutil import copytree

import pytest

from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.models import DiscoverySuccess
from replayforge.evidence.discovery_capture import ScenarioCaptureRequest
from replayforge.evidence.scenario_restore import restore_scenario
from replayforge.runs.discovery_suite import _merge_scenarios
from tests.unit.runs.test_discovery_suite import service_for

ROOT = Path(__file__).resolve().parents[4]


def request() -> ScenarioCaptureRequest:
    return ScenarioCaptureRequest(
        code="member_not_found",
        kind="business_outcome",
        goal="Observe missing member",
        inputs={"member_id": "00000", "payoff_date": "2026-09-20"},
    )


def test_restoration_uses_actual_recorded_boundary_and_original_run() -> None:
    scenario = restore_scenario(ROOT / "evidence/discovery-payoff-member-not-found", request())
    assert isinstance(scenario.result, DiscoverySuccess)
    assert scenario.result.run_id == "run_d3c8fb453e444d94adb7134fdaeed32b"
    assert scenario.result.branch is not None
    assert scenario.result.branch.after_step_count == 2
    assert scenario.result.branch.condition.kind == "rendered_text"
    assert scenario.result.evidence_manifest.endswith(
        "manifest-evd_d811385a37c046e98262601fd123d4a0.bin"
    )
    assert "00000" not in repr(scenario)


def test_successful_primary_without_observed_branch_is_not_a_scenario() -> None:
    with pytest.raises(ValueError, match="branch evidence"):
        restore_scenario(ROOT / "evidence/discovery-servicing-loan-payoff", request())


def test_altered_evidence_is_rejected_before_restoration(tmp_path: Path) -> None:
    source = ROOT / "evidence/discovery-payoff-member-not-found"
    destination = tmp_path / source.name
    copytree(source, destination)
    (destination / "events.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="hash or size"):
        restore_scenario(destination, request())


def test_restored_suite_still_requires_publication_validation() -> None:
    artifact = load_artifact_yaml(
        (ROOT / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
    )
    # The service's validator is a unit-test stub, not genuine replay evidence.
    service = service_for(artifact)
    service.registry.publish(artifact)
    suite = service.from_published(
        capability_id=artifact.capability.id,
        version=artifact.capability.version,
        tenant="harbor",
        inputs={"member_id": "12345", "payoff_date": "2026-09-20"},
    )
    scenario = restore_scenario(ROOT / "evidence/discovery-payoff-member-not-found", request())
    restored = service.restore_scenarios(suite.suite_id, (scenario,))
    assert restored.status == "collecting"
    assert restored.artifact is None and restored.published_version is None
    assert restored.scenarios == (scenario,)
    with pytest.raises(ValueError, match="empty collecting"):
        service.restore_scenarios(suite.suite_id, (scenario,))
    with pytest.raises(ValueError, match="declared disposition"):
        service.finalize(suite.suite_id)


def test_matching_lookups_cannot_import_another_tasks_exception() -> None:
    primary = load_artifact_yaml(
        (ROOT / "capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
    )
    scenario = restore_scenario(ROOT / "evidence/discovery-payoff-member-not-found", request())
    assert isinstance(scenario.result, DiscoverySuccess)
    foreign = scenario.result.artifact.model_copy(
        update={
            "capability": scenario.result.artifact.capability.model_copy(
                update={"id": "another.task"}
            ),
        }
    )
    with pytest.raises(ValueError, match="primary task contract"):
        _merge_scenarios(
            primary, (replace(scenario, result=replace(scenario.result, artifact=foreign)),)
        )
