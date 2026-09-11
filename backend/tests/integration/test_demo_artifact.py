from pathlib import Path

import pytest

from replayforge.capabilities.serialization import artifact_content_hash, load_artifact_yaml

pytestmark = pytest.mark.integration
REPOSITORY = Path(__file__).resolve().parents[3]
ARTIFACTS = tuple(
    sorted((REPOSITORY / "capabilities" / "member.lookup_savings_balance").glob("*.yaml"))
)


@pytest.mark.parametrize("artifact_path", ARTIFACTS, ids=lambda path: path.stem)
def test_reviewed_demo_artifacts_are_valid_and_hash_locked(artifact_path: Path) -> None:
    artifact = load_artifact_yaml(artifact_path.read_text())
    recovery_steps = tuple(step for recovery in artifact.recoveries for step in recovery.steps)

    assert artifact.provenance.artifact_content_hash == artifact_content_hash(artifact)
    assert artifact.compatibility.supported_variants == ("harbor", "summit")
    assert all(
        candidate.strategy != "coordinates"
        for step in (*artifact.steps, *recovery_steps)
        if step.target is not None
        for candidate in step.target.candidates
    )
