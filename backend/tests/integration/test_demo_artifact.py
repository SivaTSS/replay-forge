from pathlib import Path

import pytest
import yaml

from replayforge.capabilities.serialization import artifact_content_hash, load_artifact_yaml

pytestmark = pytest.mark.integration
REPOSITORY = Path(__file__).resolve().parents[3]
ARTIFACTS = tuple(sorted((REPOSITORY / "capabilities").glob("*/*.yaml")))


@pytest.mark.parametrize("artifact_path", ARTIFACTS, ids=lambda path: path.stem)
def test_genuinely_discovered_workstation_artifacts_are_valid_and_hash_locked(
    artifact_path: Path,
) -> None:
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


@pytest.mark.parametrize("artifact_path", ARTIFACTS, ids=lambda path: path.parent.name)
def test_geometry_free_artifact_contains_only_semantic_visual_identity(artifact_path: Path) -> None:
    raw = yaml.safe_load(artifact_path.read_text())
    forbidden = {
        "x",
        "y",
        "width",
        "height",
        "viewport_width",
        "viewport_height",
        "search_region",
        "relative_region",
        "relative_search_region",
        "minimum_score",
        "uniqueness_margin",
        "minimum_scale",
        "maximum_scale",
        "scale_step",
    }

    def walk(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).intersection(forbidden) | set().union(
                *(walk(item) for item in value.values())
            )
        if isinstance(value, list):
            return set().union(*(walk(item) for item in value))
        return set()

    assert raw["schema_version"] == "1.4"
    assert raw["provenance"]["provider"] == "openai"
    assert raw["compatibility"]["entry_point"] == "legacy_servicing"
    assert all(not step.get("target", {}).get("candidates") for step in raw["steps"])
    assert walk(raw) == set()


def test_card_lock_trace_checks_identity_on_both_sides_of_the_change() -> None:
    artifact = load_artifact_yaml(
        (REPOSITORY / "capabilities/member.temporary_card_lock/1.0.1.yaml").read_text()
    )
    mutations = [
        index for index, step in enumerate(artifact.steps) if step.risk.value == "reversible"
    ]
    identities = [
        index
        for index, step in enumerate(artifact.steps)
        if step.action.kind == "extract"
        and step.action.output == "card_id"
        and any(condition.kind == "identity_matches" for condition in step.postconditions)
    ]
    assert len(mutations) == 1
    assert len(identities) == 2
    assert identities[0] < mutations[0] < identities[1]
    assert any(
        condition.kind == "output_equals"
        and condition.output == "lock_status"
        and condition.value == "Temporarily locked"
        for step in artifact.steps[mutations[0] + 1 :]
        for condition in step.postconditions
    )
