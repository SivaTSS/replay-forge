from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.discovery.privacy import validate_artifact_privacy
from replayforge.evidence.redaction import EvidenceRejectedError


@pytest.mark.parametrize(
    "value", ["Synthetic Person", "12345", "synthetic@example.invalid", "123-45-6789"]
)
def test_artifact_metadata_cannot_retain_invocation_or_personal_shaped_values(
    valid_artifact_data: dict[str, Any], value: str
) -> None:
    valid_artifact_data["capability"]["description"] = f"Look up {value}."
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    with pytest.raises(EvidenceRejectedError):
        validate_artifact_privacy(artifact, {"query": value})


def test_invocation_examples_are_not_exempt_from_artifact_privacy(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = CapabilityArtifact.model_validate(valid_artifact_data)
    with pytest.raises(EvidenceRejectedError, match="literal invocation"):
        validate_artifact_privacy(artifact, {"nested": {"member_id": "12345"}})


def test_structural_artifact_without_invocation_literals_is_allowed(
    valid_artifact_data: dict[str, Any],
) -> None:
    valid_artifact_data["inputs"]["properties"]["member_id"].pop("example", None)
    validate_artifact_privacy(
        CapabilityArtifact.model_validate(valid_artifact_data), {"member_id": "12345"}
    )
