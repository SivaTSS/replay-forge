from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import (
    CapabilityArtifact,
    ClickAction,
    LocatorBundle,
    LocatorCandidate,
    LocatorStrategy,
    NavigateAction,
)
from replayforge.discovery.models import (
    ActProposal,
    CapabilityDraftSpec,
    DiscoverySuccess,
    RecordedDiscoveryStep,
)
from replayforge.policy.types import Risk
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


def observation() -> NormalizedObservation:
    return NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=datetime(2026, 9, 10, tzinfo=UTC),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="frame-state",
        landmarks=("Member Search",),
    )


def test_nested_secret_cannot_hide_inside_a_public_discovery_contract(
    valid_artifact_data: dict[str, Any],
) -> None:
    inputs = valid_artifact_data["inputs"]
    inputs["properties"]["member_id"] = {
        "type": "object",
        "description": "Member",
        "data_classification": "public",
        "properties": {
            "value": {"type": "string", "description": "Value", "data_classification": "secret"}
        },
    }
    with pytest.raises(ValueError, match="forbidden field"):
        CapabilityDraftSpec(
            operation_slug="lookup_member",
            name="Lookup",
            description="Read member",
            inputs=inputs,
            outputs=valid_artifact_data["outputs"],
            risk=Risk.READ_ONLY,
        )


def test_discovery_actions_enforce_target_pairing() -> None:
    with pytest.raises(ValueError, match="click action requires a target"):
        ActProposal(
            kind="act",
            action=ClickAction(kind="click"),
            rationale="Submit the search.",
            expected_effect="Search results appear.",
            declared_risk=Risk.READ_ONLY,
            confidence=0.9,
        )
    with pytest.raises(ValueError, match="cannot declare a target"):
        RecordedDiscoveryStep(
            action=NavigateAction(kind="navigate", entry_point="member_search"),
            target=LocatorBundle(
                description="Member ID field",
                candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Member ID"),),
            ),
            observation_before=observation(),
            observation_after=observation(),
            expected_effect="Search opens.",
            rationale="Start from the registered entry point.",
            risk=Risk.READ_ONLY,
        )


def test_discovery_success_binds_evidence_to_run(valid_artifact_data: dict[str, Any]) -> None:
    run_id = str(new_id(EntityKind.RUN))
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)

    result = DiscoverySuccess(
        status="success",
        run_id=run_id,
        artifact=artifact,
        evidence_manifest=f"evidence://{run_id}/manifest.json",
    )

    assert result.run_id == run_id
    with pytest.raises(ValueError, match="belong to its run"):
        DiscoverySuccess(
            status="success",
            run_id=run_id,
            artifact=artifact,
            evidence_manifest="evidence://run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/manifest.json",
        )
