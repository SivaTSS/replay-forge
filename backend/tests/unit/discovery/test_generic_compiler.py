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
