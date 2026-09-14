"""Restore observed branch metadata from verified local evidence, never from guessed steps."""

import json
from pathlib import Path

from replayforge.capabilities.models import AssertAction
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.discovery.models import DiscoverySuccess, ObservedBranch
from replayforge.evidence.discovery_capture import ScenarioCaptureRequest
from replayforge.evidence.export import verify_evidence_bundle
from replayforge.evidence.models import EventEvidence
from replayforge.runs.discovery_suite import DiscoveryScenario


def restore_scenario(directory: Path, request: ScenarioCaptureRequest) -> DiscoveryScenario:
    verified = verify_evidence_bundle(directory)
    artifact = load_artifact_yaml((directory / "artifact.yaml").read_text())
    result = json.loads((directory / "result.json").read_text())
    events = tuple(
        EventEvidence.model_validate_json(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
        if line
    )
    markers = [event for event in events if event.event_type == "branch_observed"]
    types = {event.event_type for event in events}
    if (
        result.get("status") != "success"
        or artifact.provenance.provider != "openai"
        or artifact.provenance.discovery_run_id != verified.run_id
        or len(markers) != 1
        or not {"discovery_started", "model_proposal_received", "artifact_compiled"} <= types
        or any(event.event_type.startswith("human_") for event in events)
    ):
        raise ValueError("scenario requires successful, unassisted model-driven branch evidence")
    offset = markers[0].details.get("after_step_count")
    if type(offset) is not int or not 1 <= offset < len(artifact.steps):
        raise ValueError("branch event has no valid trace boundary")
    action = artifact.steps[offset].action
    if (
        not isinstance(action, AssertAction)
        or markers[0].details.get("condition_kind") != action.condition.kind
    ):
        raise ValueError("branch event does not identify its recorded assertion")
    return DiscoveryScenario(
        kind=request.kind,
        code=request.code,
        goal=request.goal,
        description=request.goal,
        inputs=request.inputs,
        result=DiscoverySuccess(
            status="success",
            run_id=verified.run_id,
            artifact=artifact,
            evidence_manifest=result["evidence_manifest"],
            branch=ObservedBranch(offset, action.condition),
        ),
    )
