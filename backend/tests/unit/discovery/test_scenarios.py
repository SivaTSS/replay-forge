"""Scenario proposals are live assertions, not caller-written artifact branches."""

from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    CapabilityArtifact,
    Condition,
    IdentityMatchesCondition,
    NotCondition,
    TextCondition,
)
from replayforge.discovery.compiler import TraceArtifactCompiler
from replayforge.discovery.engine import DiscoveryEngine
from replayforge.discovery.models import (
    BranchProposal,
    CompleteProposal,
    DiscoverySuccess,
    ObservedBranch,
    RecordedActionProposal,
    ScenarioContext,
)
from replayforge.discovery.scenarios import scenario_expected_condition
from replayforge.runs.results import FailureResult
from replayforge.surfaces.models import NormalizedObservation, SurfaceError
from replayforge.surfaces.ports import SurfaceSession
from tests.artifacts import sample_artifact
from tests.unit.discovery.test_engine import QueueModelProvider, build_discovery, make_request
from tests.unit.replay.test_engine import FakeSurfaceSession


class HashedSurface(FakeSurfaceSession):
    def observe(self) -> NormalizedObservation:
        observation = super().observe()
        return replace(
            observation, fingerprint=sha256(observation.fingerprint.encode()).hexdigest()
        )


@pytest.mark.parametrize("wrapper", ["direct", "all", "any", "not"])
def test_scenario_cannot_drop_nested_identity_or_its_new_assertion(wrapper: str) -> None:
    identity = IdentityMatchesCondition(
        kind="identity_matches", extracted_output="member_id", input_path="member_id"
    )
    guards: dict[str, Condition] = {
        "direct": identity,
        "all": AllCondition(kind="all", conditions=(identity,)),
        "any": AnyCondition(kind="any", conditions=(identity,)),
        "not": NotCondition(kind="not", condition=identity),
    }
    guarded = guards[wrapper]
    proposed = TextCondition(kind="text", value="Exceptional state")
    step = sample_artifact().steps[0].model_copy(update={"postconditions": (guarded,)})
    assert scenario_expected_condition(step, proposed) == AllCondition(
        kind="all", conditions=(guarded, proposed)
    )
    assert scenario_expected_condition(step, None) == guarded
    assert scenario_expected_condition(step, guarded) == guarded


def test_scenario_without_identity_can_replace_success_only_expectation() -> None:
    step = (
        sample_artifact()
        .steps[0]
        .model_copy(update={"postconditions": (TextCondition(kind="text", value="Success"),)})
    )
    assert scenario_expected_condition(step, None) is None


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
    assert len(provider.calls) == 2  # No further model action after a verified terminal marker.
    assert len(provider.proposals) == 1
    assert surface.closed


def test_unobserved_branch_is_not_compiled() -> None:
    engine, _provider, primary, surface = scenario_engine(
        TextCondition(kind="text", value="No member found"), observed=False
    )
    result = engine.execute(make_request(scenario=ScenarioContext(primary, "business_outcome")))
    assert isinstance(result, FailureResult)
    assert result.code == "action_condition_not_verified"
    assert surface.closed


@pytest.mark.parametrize(
    "blocked, missing, ready", [(True, False, True), (False, True, False), (False, False, True)]
)
def test_recovery_rejoin_requires_next_target_even_if_original_notice_remains_visible(
    blocked: bool,
    missing: bool,
    ready: bool,
) -> None:
    condition = TextCondition(kind="text", value="No member found")
    engine, _provider, primary, surface = scenario_engine(condition, observed=blocked)
    surface.resolve_error = (
        SurfaceError("target_absent", "Rejoin target is off screen.") if missing else None
    )
    surface.resolve_failures_remaining = -1
    request = make_request(scenario=ScenarioContext(primary, "recovery"))
    assert engine.effective_policy is not None
    assert (
        engine._rejoin_ready(
            request,
            cast(SurfaceSession, surface),
            ObservedBranch(1, condition),
            {},
            engine.effective_policy,
        )
        is ready
    )
    assert surface.executed_targets == []  # Readiness never clicks the next primary target.


def test_unready_recovery_completion_is_replanned_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from replayforge.discovery.models import EscalateProposal

    condition = TextCondition(kind="text", value="No member found")
    engine, provider, primary, _surface = scenario_engine(condition)
    monkeypatch.setattr(DiscoveryEngine, "_rejoin_ready", staticmethod(lambda *_args: False))
    provider.proposals.append(
        EscalateProposal(
            kind="escalate",
            reason_code="needs_reorientation",
            rationale="The rejoin surface is unavailable",
        )
    )
    result = engine.execute(make_request(scenario=ScenarioContext(primary, "recovery")))
    assert not isinstance(result, DiscoverySuccess)
    assert any("Completion rejected" in item for item in provider.calls[-1].action_history)


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
