"""Distribution acceptance checks, not task-specific runtime/compiler rules."""

from pathlib import Path
from typing import Any

import pytest

from replayforge.capabilities.registry import LocalCapabilityRegistry
from replayforge.shared.yaml import load_unique_yaml

REPOSITORY = Path(__file__).resolve().parents[4]
WORKFLOWS = load_unique_yaml((REPOSITORY / "config/servicing-discovery.yaml").read_text())[
    "workflows"
]


def _input_bindings(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["path"]} if value.get("source") == "input" else set()
        return own.union(*(_input_bindings(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_input_bindings(item) for item in value))
    return set()


@pytest.mark.parametrize("workflow", tuple(WORKFLOWS))
def test_current_examples_bind_every_input_in_execution_and_cover_declared_cases(
    workflow: str,
) -> None:
    spec = WORKFLOWS[workflow]
    artifact = (
        LocalCapabilityRegistry(REPOSITORY / "capabilities")
        .latest(spec["expected_capability_id"])
        .artifact
    )

    assert set(artifact.inputs.properties) == set(spec["expected_inputs"])
    assert set(artifact.outputs.properties) == set(spec["expected_outputs"])
    assert artifact.capability.risk.value == spec["expected_risk"]
    assert set(artifact.compatibility.supported_variants) == {
        spec["tenant"],
        *spec["validation_tenants"],
    }
    # A final comparison alone cannot make a default-dependent lookup parameterized.
    used = set().union(
        *(
            _input_bindings(
                {
                    "action": step.action.model_dump(mode="json"),
                    "target": step.target.model_dump(mode="json") if step.target else None,
                }
            )
            for step in artifact.steps
        )
    )
    assert used == set(spec["expected_inputs"])
    observed = {
        *(("business_outcome", outcome.code) for outcome in artifact.outcomes),
        *(("application_failure", failure.code) for failure in artifact.failures),
        *(("recovery", recovery.id) for recovery in artifact.recoveries),
    }
    assert observed == {(scenario["kind"], scenario["code"]) for scenario in spec["scenarios"]}
