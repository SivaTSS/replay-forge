from typing import Any

import pytest

from replayforge.capabilities.models import CapabilityArtifact, OutputEqualsCondition
from replayforge.discovery.conditions import validate_condition_bindings
from replayforge.policy.types import Risk
from replayforge.providers.openai import (
    ProviderCondition,
    ProviderExtractAction,
    ProviderExtractLocatorBundle,
    ProviderExtractProposal,
    ProviderRenderedFieldValueCandidate,
    _proposal_payload,
)
from replayforge.surfaces.models import SurfaceError
from replayforge.surfaces.playwright import PlaywrightSurfaceSession


@pytest.mark.parametrize("actual", [None, "Open", "closed", "Closed ", "Closed"])
def test_business_state_comparison_is_exact(actual: str | None) -> None:
    # Output-only predicates require no browser state.
    session = object.__new__(PlaywrightSurfaceSession)
    condition = OutputEqualsCondition(kind="output_equals", output="ticket_state", value="Closed")
    assert session.evaluate(condition, {"ticket_state": actual}, {}) is (actual == "Closed")
    with pytest.raises(SurfaceError, match="not been captured"):
        validate_condition_bindings(condition, {}, {})


def test_output_equality_requires_declared_output(valid_artifact_data: dict[str, Any]) -> None:
    valid_artifact_data["checkpoint"]["condition"] = {
        "kind": "output_equals",
        "output": "undeclared",
        "value": "Closed",
    }
    with pytest.raises(ValueError, match="unknown output"):
        CapabilityArtifact.model_validate(valid_artifact_data)


def test_provider_extraction_can_verify_its_effect_without_an_extra_model_action() -> None:
    proposal = ProviderExtractProposal(
        kind="act",
        action=ProviderExtractAction(kind="extract", output="ticket_state"),
        target=ProviderExtractLocatorBundle(
            description="State field",
            visual_candidates=(
                ProviderRenderedFieldValueCandidate(strategy="rendered_field_value", label="State"),
            ),
        ),
        rationale="Read final state",
        expected_effect="Required state verified",
        declared_risk=Risk.READ_ONLY,
        confidence=1,
        expected_condition=ProviderCondition(
            kind="output_equals", operand="ticket_state", secondary_operand="Closed"
        ),
    )
    assert _proposal_payload(proposal)["expected_condition"] == {
        "kind": "output_equals",
        "output": "ticket_state",
        "value": "Closed",
    }
