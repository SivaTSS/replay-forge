"""Scenario proposals are live assertions, not caller-written artifact branches."""

from dataclasses import replace
from hashlib import sha256

import pytest

from replayforge.capabilities.models import CapabilityArtifact, TextCondition
from replayforge.discovery.compiler import TraceArtifactCompiler
from replayforge.discovery.engine import DiscoveryEngine
from replayforge.discovery.models import (
    BranchProposal,
    CompleteProposal,
    DiscoverySuccess,
    RecordedActionProposal,
    ScenarioContext,
)
from replayforge.runs.results import FailureResult
from replayforge.surfaces.models import NormalizedObservation
from tests.artifacts import sample_artifact
from tests.unit.discovery.test_engine import QueueModelProvider, build_discovery, make_request
from tests.unit.replay.test_engine import FakeSurfaceSession


class HashedSurface(FakeSurfaceSession):
    def observe(self) -> NormalizedObservation:
        observation = super().observe()
        return replace(
            observation, fingerprint=sha256(observation.fingerprint.encode()).hexdigest()
        )


def scenario_engine(
    condition: TextCondition, *, observed: bool = True
) -> tuple[DiscoveryEngine, QueueModelProvider, CapabilityArtifact, FakeSurfaceSession]:
    primary = sample_artifact()
    data = primary.model_dump(mode="python")
    data["inputs"]["properties"]["member_id"].pop("example", None)
    primary = CapabilityArtifact.model_validate(data)
    provider = QueueModelProvider(
        [
            RecordedActionProposal(
                kind="recorded_action",
                step_id=primary.steps[0].id,
                rationale="Repeat the observed lookup",
            ),
            BranchProposal(
                kind="branch", condition=condition, rationale="Observe the exceptional state"
            ),
            CompleteProposal(kind="complete", rationale="Exceptional state verified"),
        ]
    )
    surface = HashedSurface(member_not_found=observed)
    engine, _ = build_discovery(surface, provider, primary)
    assert engine.effective_policy is not None
    engine = replace(
        engine,
        artifact_compiler=TraceArtifactCompiler(engine.clock),
        effective_policy=replace(
            engine.effective_policy,
            allowed_action_types=frozenset({"type", "click", "extract", "assert"}),
        ),
    )
    return engine, provider, primary, surface


def test_negative_trace_requires_no_invented_output_and_keeps_verified_marker() -> None:
    condition = TextCondition(kind="text", value="No member found")
    engine, provider, primary, surface = scenario_engine(condition)
    result = engine.execute(make_request(scenario=ScenarioContext(primary, "business_outcome")))
    assert isinstance(result, DiscoverySuccess), result
    assert result.artifact.outputs.required == ()
    assert result.branch is not None and result.branch.after_step_count == 1
    assert result.branch.condition == condition
    assert result.artifact.steps[-1].action.kind == "assert"
    assert provider.calls[0].reference_steps == primary.steps
    assert provider.calls[-1].branch_observed
    assert surface.closed


def test_unobserved_branch_is_not_compiled() -> None:
    engine, _provider, primary, surface = scenario_engine(
        TextCondition(kind="text", value="No member found"), observed=False
    )
    result = engine.execute(make_request(scenario=ScenarioContext(primary, "business_outcome")))
    assert isinstance(result, FailureResult)
    assert result.code == "action_condition_not_verified"
    assert surface.closed


@pytest.mark.parametrize("mode", [None, "business_outcome"])
def test_reference_action_is_not_an_arbitrary_step_id(mode: str | None) -> None:
    engine, provider, primary, _surface = scenario_engine(
        TextCondition(kind="text", value="Notice")
    )
    provider.proposals[:] = [
        RecordedActionProposal(
            kind="recorded_action", step_id="missing_step", rationale="Unknown action"
        )
    ]
    result = engine.execute(
        make_request(scenario=ScenarioContext(primary, "business_outcome") if mode else None)
    )
    assert isinstance(result, FailureResult)
    assert result.code == "scenario_reference_invalid"
