"""Reject unsafe recorded traces before they become reusable capabilities."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import (
    AllCondition,
    AnyCondition,
    CapabilityArtifact,
    ExtractAction,
    InputValue,
    Landmark,
    LiteralValue,
    LocatorBundle,
    LocatorCandidate,
    LocatorStrategy,
    NotCondition,
    OutputValidCondition,
    RouteCondition,
    TypeAction,
)
from replayforge.discovery.compiler import CompilationError, TraceArtifactCompiler
from replayforge.discovery.models import CapabilityDraftSpec, RecordedDiscoveryStep
from replayforge.policy.types import Risk
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport

CLOCK = FrozenClock(datetime(2026, 9, 10, tzinfo=UTC))


@pytest.fixture
def compilation(valid_artifact_data: dict[str, Any]) -> dict[str, Any]:
    source = CapabilityArtifact.model_validate(valid_artifact_data)
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=CLOCK.now(),
        route="/details",
        viewport=Viewport(1280, 800),
        fingerprint="a" * 64,
        landmarks=("Details",),
    )
    step = RecordedDiscoveryStep(
        action=ExtractAction(kind="extract", output="available_balance"),
        target=LocatorBundle(
            description="Balance",
            candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Balance"),),
        ),
        observation_before=observation,
        observation_after=observation,
        expected_effect="Read the displayed amount.",
        rationale="The final detail view was verified.",
        risk=Risk.READ_ONLY,
        verified_postconditions=(RouteCondition(kind="route", pattern="/details"),),
    )
    run_id = str(new_id(EntityKind.RUN))
    return {
        "draft": CapabilityDraftSpec(
            operation_slug="read_value",
            name="Read value",
            description="Read a verified detail value.",
            inputs=source.inputs,
            outputs=source.outputs,
            risk=Risk.READ_ONLY,
        ),
        "steps": (step,),
        "run_id": run_id,
        "goal": "Read a value",
        "application_family": "example_app",
        "tenant": "tenant",
        "entry_point": "details",
        "final_observation": observation,
        "provider_name": "test",
        "model_name": "test",
        "evidence_manifest": f"evidence://{run_id}/manifest.json",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_draft", "draft is required"),
        ("unverified_completion", "completion lacks"),
        ("undeclared_input", "undeclared input"),
        ("undeclared_output", "undeclared output"),
        ("missing_output", "missing required outputs"),
        ("risk_ceiling", "risk exceeds"),
        ("action_ceiling", "action outside"),
        ("route_ceiling", "route outside"),
        ("required_landmark", "landmarks were not confirmed"),
        ("forbidden_landmark", "forbidden application landmark"),
    ],
)
def test_compilation_fails_closed(compilation: dict[str, Any], mutation: str, message: str) -> None:
    step = compilation["steps"][0]
    if mutation == "missing_draft":
        compilation.pop("draft")
    elif mutation == "unverified_completion":
        compilation["steps"] = (replace(step, verified_postconditions=()),)
    elif mutation == "undeclared_input":
        compilation["steps"] = (
            replace(
                step,
                action=TypeAction(kind="type", value=InputValue(source="input", path="missing")),
            ),
            step,
        )
    elif mutation == "undeclared_output":
        compilation["steps"] = (
            replace(step, action=ExtractAction(kind="extract", output="missing")),
        )
    elif mutation == "missing_output":
        compilation["steps"] = (
            replace(
                step,
                action=TypeAction(
                    kind="type",
                    value=LiteralValue(
                        source="literal",
                        value="label",
                    ),
                ),
            ),
        )
    elif mutation == "risk_ceiling":
        compilation["steps"] = (replace(step, risk=Risk.REVERSIBLE),)
        compilation["maximum_risk"] = Risk.READ_ONLY
    elif mutation == "action_ceiling":
        compilation["allowed_action_types"] = frozenset({"type"})
    elif mutation == "route_ceiling":
        compilation["allowed_route_patterns"] = frozenset({"/elsewhere", "/other/path"})
    elif mutation == "required_landmark":
        compilation["required_landmarks"] = (Landmark(kind="heading", value="Missing"),)
    else:
        compilation["forbidden_landmarks"] = (Landmark(kind="heading", value="Details"),)
    with pytest.raises(CompilationError, match=message):
        TraceArtifactCompiler(CLOCK).compile(**compilation)


def test_compilation_chooses_narrowest_registered_route(compilation: dict[str, Any]) -> None:
    compilation["allowed_route_patterns"] = frozenset({"/*", "/details", "/unrelated/path"})
    artifact = TraceArtifactCompiler(CLOCK).compile(**compilation)
    assert artifact.policy.allowed_route_patterns == frozenset({"/details"})


def test_compiler_retains_before_and_after_captures(compilation: dict[str, Any]) -> None:
    step = compilation["steps"][0]
    compilation["steps"] = (step, step)
    artifact = TraceArtifactCompiler(CLOCK).compile(**compilation)
    assert len(artifact.steps) == 2
    assert artifact.steps[0].action == artifact.steps[1].action
    assert artifact.steps[0].id != artifact.steps[1].id


@pytest.mark.parametrize("kind", ["all", "any", "not", "not_output"])
def test_completion_requires_surface_evidence_inside_logical_conditions(
    compilation: dict[str, Any], kind: str
) -> None:
    output = OutputValidCondition(kind="output_valid", output="available_balance")
    route = RouteCondition(kind="route", pattern="/details")
    conditions = {
        "all": AllCondition(kind="all", conditions=(output, route)),
        "any": AnyCondition(kind="any", conditions=(route, output)),
        "not": NotCondition(kind="not", condition=RouteCondition(kind="route", pattern="/error")),
        "not_output": NotCondition(kind="not", condition=output),
    }
    compilation["steps"] = (
        replace(compilation["steps"][0], verified_postconditions=(conditions[kind],)),
    )
    if kind == "not_output":
        with pytest.raises(CompilationError, match="completion lacks"):
            TraceArtifactCompiler(CLOCK).compile(**compilation)
    else:
        artifact = TraceArtifactCompiler(CLOCK).compile(**compilation)
        assert isinstance(artifact.checkpoint.condition, AllCondition)
        assert conditions[kind] in artifact.checkpoint.condition.conditions


def test_unplanned_compiler_has_no_implicit_task_outputs() -> None:
    assert TraceArtifactCompiler(CLOCK).output_contract.properties == {}
