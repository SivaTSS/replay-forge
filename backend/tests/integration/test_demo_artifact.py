from pathlib import Path

import pytest

from replayforge.capabilities.serialization import artifact_content_hash, load_artifact_yaml

pytestmark = pytest.mark.integration


def test_reviewed_demo_artifact_is_valid_and_hash_locked() -> None:
    repository = Path(__file__).resolve().parents[3]
    artifact_path = repository / "capabilities" / "member.lookup_savings_balance" / "1.0.0.yaml"

    artifact = load_artifact_yaml(artifact_path.read_text())

    assert artifact.provenance.artifact_content_hash == artifact_content_hash(artifact)
    assert artifact.compatibility.supported_variants == ("harbor", "summit")
    assert all(
        candidate.strategy != "coordinates"
        for step in artifact.steps
        if step.target is not None
        for candidate in step.target.candidates
    )
