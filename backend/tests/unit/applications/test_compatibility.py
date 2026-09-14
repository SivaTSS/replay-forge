from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from replayforge.applications.compatibility import (
    validate_application_compatibility,
    validate_rendered_readiness,
)
from replayforge.applications.registry import load_application_registry
from replayforge.capabilities.models import CapabilityArtifact, Landmark
from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.surfaces.models import SurfaceError
from replayforge.surfaces.ports import SurfaceSession


@pytest.mark.parametrize("required_present,forbidden_present", [(False, False), (True, True)])
def test_rendered_readiness_requires_registered_landmarks(
    required_present: bool, forbidden_present: bool
) -> None:
    launch = load_application_registry(Path("config/applications.yaml")).resolve(
        "northstar_member_service", "summit", "legacy_servicing"
    )
    launch = replace(
        launch, forbidden_landmarks=(Landmark(kind="visual_text", value="Unavailable"),)
    )
    session = Mock(spec=SurfaceSession)
    session.wait_until.return_value = required_present
    session.evaluate.return_value = forbidden_present
    with pytest.raises(SurfaceError, match="readiness contract"):
        validate_rendered_readiness(session, launch)


def test_every_committed_capability_matches_its_registered_tenants() -> None:
    registry = load_application_registry(Path("config/applications.yaml"))
    for path in Path("capabilities").glob("*/*.yaml"):
        artifact = load_artifact_yaml(path.read_text())
        for tenant in artifact.compatibility.supported_variants:
            validate_application_compatibility(registry, artifact, tenant)


@pytest.mark.parametrize(
    "field,value",
    [("surface_contract", "web.v2"), ("base_variant", "changed"), ("rendered_surface", False)],
)
def test_changed_contract_is_rejected(field: str, value: Any) -> None:
    registry = load_application_registry(Path("config/applications.yaml"))
    artifact = load_artifact_yaml(
        Path("capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
    )
    changed = artifact.model_copy(
        update={"compatibility": artifact.compatibility.model_copy(update={field: value})}
    )
    with pytest.raises(SurfaceError) as error:
        validate_application_compatibility(registry, changed, "harbor")
    assert error.value.code == "application_contract_mismatch"


def test_unknown_application_and_entry_points_are_rejected(
    valid_artifact_data: dict[str, Any],
) -> None:
    registry = load_application_registry(Path("config/applications.yaml"))
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    with pytest.raises(SurfaceError, match="not registered"):
        validate_application_compatibility(registry, artifact, "unregistered")
    artifact = load_artifact_yaml(
        Path("capabilities/member.servicing_loan_payoff_quote/1.0.1.yaml").read_text()
    )
    changed = artifact.model_copy(
        update={
            "policy": artifact.policy.model_copy(
                update={"allowed_entry_points": frozenset({"missing"})}
            )
        }
    )
    with pytest.raises(SurfaceError, match="no longer registered"):
        validate_application_compatibility(registry, changed, "harbor")
