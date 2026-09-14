"""Small synthetic contracts for unit tests; never registered in the demo runtime."""

from pathlib import Path

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.capabilities.serialization import artifact_content_hash, load_artifact_yaml
from replayforge.policy.types import Risk


def sample_artifact(*, sensitive: bool = False) -> CapabilityArtifact:
    source = Path(__file__).parent / "fixtures/catalog/member.lookup_savings_balance/1.0.0.yaml"
    artifact = load_artifact_yaml(source.read_text())
    if sensitive:
        steps = list(artifact.steps)
        steps[1] = steps[1].model_copy(update={"risk": Risk.SENSITIVE})
        artifact = artifact.model_copy(
            update={
                "steps": tuple(steps),
                "capability": artifact.capability.model_copy(
                    update={"risk": Risk.SENSITIVE, "version": "2.0.0"}
                ),
                "policy": artifact.policy.model_copy(update={"maximum_risk": Risk.SENSITIVE}),
            }
        )
    artifact = CapabilityArtifact.model_validate(artifact.model_dump(mode="python"))
    return artifact.model_copy(
        update={
            "provenance": artifact.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(artifact)}
            )
        }
    )
