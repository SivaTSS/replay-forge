from datetime import UTC, datetime

import pytest

from replayforge.capabilities.models import (
    ExtractAction,
    InputValue,
    JsonValueType,
    LocatorBundle,
    LocatorCandidate,
    LocatorStrategy,
    ObjectContract,
    RenderedFieldValueCandidate,
    RouteCondition,
    TypeAction,
    ValueSchema,
)
from replayforge.discovery.compiler import CompilationError, TraceArtifactCompiler
from replayforge.discovery.models import CapabilityDraftSpec, RecordedDiscoveryStep
from replayforge.policy.types import DataClassification, Risk
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport


def test_compiler_accepts_a_different_task_shape() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="different-task",
        landmarks=("Member Search",),
    )
    target = LocatorBundle(
        description="Reference field",
        candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Reference"),),
    )
    draft = CapabilityDraftSpec(
        operation_slug="read_reference",
        name="Read reference",
        description="Read the reference value visible after navigation.",
        inputs=ObjectContract(
            required=("reference_id",),
            properties={
                "reference_id": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Reference identifier.",
                    data_classification=DataClassification.CUSTOMER_IDENTIFIER,
                )
            },
        ),
        outputs=ObjectContract(
            required=("reference_value",),
            properties={
                "reference_value": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Displayed reference value.",
                    data_classification=DataClassification.PERSONAL,
                )
            },
        ),
        risk=Risk.READ_ONLY,
    )
    steps = (
        RecordedDiscoveryStep(
            TypeAction(kind="type", value=InputValue(source="input", path="reference_id")),
            target,
            observation,
            observation,
            "The reference is entered.",
            "The field is visible.",
            Risk.READ_ONLY,
        ),
        RecordedDiscoveryStep(
            ExtractAction(kind="extract", output="reference_value"),
            target,
            observation,
            observation,
            "The value is captured.",
            "The displayed value is stable.",
            Risk.READ_ONLY,
            (RouteCondition(kind="route", pattern="/members/search"),),
        ),
    )

    artifact = TraceArtifactCompiler(clock).compile(
        draft=draft,
        run_id="run_generic",
        goal="Read the reference value",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="member_search",
        steps=steps,
        final_observation=observation,
        provider_name="test",
        model_name="test",
        evidence_manifest="evidence://generic",
    )

    assert artifact.capability.id == "northstar_member_service.read_reference"
    assert artifact.outputs.required == ("reference_value",)
    assert len(artifact.steps) == 2


def test_generic_compiler_rejects_missing_output() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=clock.now(),
        route="/members/search",
        viewport=Viewport(1280, 800),
        fingerprint="missing-output",
        landmarks=("Member Search",),
    )
    draft = CapabilityDraftSpec(
        operation_slug="read_value",
        name="Read value",
        description="Read a value.",
        inputs=ObjectContract(required=(), properties={}),
        outputs=ObjectContract(
            required=("value",),
            properties={
                "value": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Value.",
                    data_classification=DataClassification.PERSONAL,
                )
            },
        ),
        risk=Risk.READ_ONLY,
    )
    with pytest.raises(CompilationError, match="trace contains no actions"):
        TraceArtifactCompiler(clock).compile(
            draft=draft,
            run_id="run_generic",
            goal="Read a value",
            application_family="app",
            tenant="tenant",
            entry_point="home",
            steps=(),
            final_observation=observation,
            provider_name="test",
            model_name="test",
            evidence_manifest="evidence://generic",
        )


def test_compiler_derives_completion_from_verified_rendered_extractions() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=clock.now(),
        route="/workbench",
        viewport=Viewport(1280, 800),
        fingerprint="rendered-details",
        landmarks=("Reference",),
    )
    draft = CapabilityDraftSpec(
        operation_slug="read_reference",
        name="Read reference",
        description="Read a rendered reference.",
        inputs=ObjectContract(required=(), properties={}),
        outputs=ObjectContract(
            required=("reference",),
            properties={
                "reference": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Displayed reference.",
                    data_classification=DataClassification.PERSONAL,
                )
            },
        ),
        risk=Risk.READ_ONLY,
    )
    target = LocatorBundle(
        description="Reference value",
        visual_candidates=(
            RenderedFieldValueCandidate(strategy="rendered_field_value", label="Reference"),
        ),
    )
    step = RecordedDiscoveryStep(
        ExtractAction(kind="extract", output="reference"),
        target,
        observation,
        observation,
        "The reference is captured.",
        "The displayed reference is visible.",
        Risk.READ_ONLY,
    )

    artifact = TraceArtifactCompiler(clock).compile(
        draft=draft,
        run_id="run_rendered",
        goal="Read a rendered reference",
        application_family="app",
        tenant="tenant",
        entry_point="workbench",
        steps=(step,),
        final_observation=observation,
        provider_name="test",
        model_name="test",
        evidence_manifest="evidence://rendered",
        rendered_surface=True,
    )

    checkpoint = artifact.checkpoint.condition
    assert checkpoint.kind == "all"
    assert any(
        condition.kind == "rendered_text" and condition.value == "Reference"
        for condition in checkpoint.conditions
    )


def test_compiler_uses_observed_action_risk_not_planner_guess() -> None:
    clock = FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC))
    observation = NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=clock.now(),
        route="/details",
        viewport=Viewport(1280, 800),
        fingerprint="details",
        landmarks=("Reference",),
    )
    draft = CapabilityDraftSpec(
        operation_slug="read_reference",
        name="Read reference",
        description="Read a reference.",
        inputs=ObjectContract(required=(), properties={}),
        outputs=ObjectContract(
            required=("reference",),
            properties={
                "reference": ValueSchema(
                    type=JsonValueType.STRING,
                    description="Displayed reference.",
                    data_classification=DataClassification.PERSONAL,
                )
            },
        ),
        risk=Risk.SENSITIVE,
    )
    target = LocatorBundle(
        description="Reference value",
        candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Reference"),),
    )
    step = RecordedDiscoveryStep(
        ExtractAction(kind="extract", output="reference"),
        target,
        observation,
        observation,
        "The reference is captured.",
        "The displayed reference is visible.",
        Risk.READ_ONLY,
        (RouteCondition(kind="route", pattern="/details"),),
    )

    artifact = TraceArtifactCompiler(clock).compile(
        draft=draft,
        run_id="run_risk",
        goal="Read a reference",
        application_family="app",
        tenant="tenant",
        entry_point="details",
        steps=(step,),
        final_observation=observation,
        provider_name="test",
        model_name="test",
        evidence_manifest="evidence://risk",
    )

    assert artifact.capability.risk is Risk.READ_ONLY
