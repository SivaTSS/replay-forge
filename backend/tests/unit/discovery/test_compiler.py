from datetime import UTC, datetime
from typing import Any

import pytest

from replayforge.capabilities.models import (
    CapabilityArtifact,
    ClickAction,
    ExtractAction,
    InputValue,
    LocatorBundle,
    OcrTextCandidate,
    RenderedTextCandidate,
    TypeAction,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.discovery.compiler import CompilationError
from replayforge.discovery.models import RecordedDiscoveryStep
from replayforge.policy.types import Risk
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import NormalizedObservation, Viewport
from tests.legacy_compiler import SavingsBalanceCompiler


def observation(fingerprint: str) -> NormalizedObservation:
    return NormalizedObservation(
        id=new_id(EntityKind.EVENT),
        session_id=new_id(EntityKind.SESSION),
        captured_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
        route="/accounts/ACC-1/details",
        viewport=Viewport(1280, 800),
        fingerprint=fingerprint,
        landmarks=("Savings",),
    )


def recording(action: Any, target: LocatorBundle, index: int) -> RecordedDiscoveryStep:
    return RecordedDiscoveryStep(
        action=action,
        target=target,
        observation_before=observation(f"before-{index}"),
        observation_after=observation(f"after-{index}"),
        expected_effect="The next workflow state is visible.",
        rationale="The visible control advances the requested workflow.",
        risk=Risk.READ_ONLY,
    )


def complete_trace(valid_artifact_data: dict[str, Any]) -> tuple[RecordedDiscoveryStep, ...]:
    source = CapabilityArtifact.model_validate(valid_artifact_data)
    field = source.steps[0].target
    button = source.steps[1].target
    value = source.steps[2].target
    assert field is not None and button is not None and value is not None
    actions = [
        TypeAction(kind="type", value=InputValue(source="input", path="member_id"), clear=True),
        ClickAction(kind="click"),
        ClickAction(kind="click"),
        *(
            ExtractAction(kind="extract", output=name)
            for name in (
                "member_id",
                "account_type",
                "currency",
                "available_balance",
                "as_of",
            )
        ),
    ]
    targets = [field, button, button, value, value, value, value, value]
    return tuple(
        recording(action, target, index)
        for index, (action, target) in enumerate(zip(actions, targets, strict=True))
    )


def compiler() -> SavingsBalanceCompiler:
    return SavingsBalanceCompiler(FrozenClock(datetime(2026, 9, 10, 12, 30, tzinfo=UTC)))


def compile_trace(trace: tuple[RecordedDiscoveryStep, ...]) -> CapabilityArtifact:
    return compiler().compile(
        run_id=new_id(EntityKind.RUN),
        goal="Look up the member and return the current savings balance.",
        application_family="northstar_member_service",
        tenant="harbor_credit_union",
        entry_point="member_search",
        steps=trace,
        final_observation=observation("final-fingerprint"),
        provider_name="test-provider",
        model_name="test-model",
        evidence_manifest="evidence://test/manifest.json",
    )


def test_compiler_emits_hashed_identity_checked_artifact(
    valid_artifact_data: dict[str, Any],
) -> None:
    artifact = compile_trace(complete_trace(valid_artifact_data))

    assert artifact.capability.id == "member.lookup_savings_balance"
    assert artifact.outputs.required == (
        "member_id",
        "account_type",
        "currency",
        "available_balance",
        "as_of",
    )
    assert artifact.provenance.artifact_content_hash == artifact_content_hash(artifact)
    assert artifact.outcomes[0].code == "member_not_found"
    checkpoint = artifact.checkpoint.model_dump_json()
    assert "identity_matches" in checkpoint
    assert "member_id" in checkpoint


def test_compiler_rejects_partial_trace(valid_artifact_data: dict[str, Any]) -> None:
    trace = complete_trace(valid_artifact_data)

    with pytest.raises(CompilationError, match="missing required"):
        compile_trace(trace[:3])


def test_compiler_emits_visual_schema_and_conditions(
    valid_artifact_data: dict[str, Any],
) -> None:
    trace = tuple(
        recording(
            item.action,
            LocatorBundle(
                description=item.target.description if item.target else "Visual target",
                registered_risk=Risk.READ_ONLY,
                visual_candidates=(OcrTextCandidate(strategy="ocr_text", value=f"Target {index}"),),
            ),
            index,
        )
        for index, item in enumerate(complete_trace(valid_artifact_data))
    )

    artifact = compile_trace(trace)

    assert artifact.schema_version == "1.1"
    assert artifact.capability.version == "3.0.0"
    assert artifact.compatibility.fingerprint.required_landmarks[0].kind == "visual_text"
    assert artifact.steps[1].postconditions[0].kind == "visual_text"
    assert artifact.steps[2].postconditions[0].kind == "visual_text"
    assert artifact.provenance.artifact_content_hash == artifact_content_hash(artifact)


def test_compiler_emits_geometry_free_schema_and_conditions(
    valid_artifact_data: dict[str, Any],
) -> None:
    trace = tuple(
        recording(
            item.action,
            LocatorBundle(
                description=item.target.description if item.target else "Rendered target",
                registered_risk=Risk.READ_ONLY,
                visual_candidates=(
                    RenderedTextCandidate(strategy="rendered_text", value=f"Target {index}"),
                ),
            ),
            index,
        )
        for index, item in enumerate(complete_trace(valid_artifact_data))
    )

    artifact = compile_trace(trace)

    assert artifact.schema_version == "1.3"
    assert artifact.capability.version == "3.2.0"
    assert artifact.steps[1].postconditions[0].kind == "rendered_text"
    assert artifact.steps[2].postconditions[0].kind == "rendered_text"
    assert artifact.provenance.artifact_content_hash == artifact_content_hash(artifact)


def test_compiler_rejects_literal_member_identifier(
    valid_artifact_data: dict[str, Any],
) -> None:
    trace = list(complete_trace(valid_artifact_data))
    target = trace[0].target
    assert target is not None
    trace[0] = recording(ClickAction(kind="click"), target, 0)

    with pytest.raises(CompilationError, match="symbolic member_id"):
        compile_trace(tuple(trace))


def test_compiler_rejects_non_read_only_trace(
    valid_artifact_data: dict[str, Any],
) -> None:
    trace = list(complete_trace(valid_artifact_data))
    trace[1] = RecordedDiscoveryStep(
        action=trace[1].action,
        target=trace[1].target,
        observation_before=trace[1].observation_before,
        observation_after=trace[1].observation_after,
        expected_effect=trace[1].expected_effect,
        rationale=trace[1].rationale,
        risk=Risk.SENSITIVE,
    )

    with pytest.raises(CompilationError, match="remain read-only"):
        compile_trace(tuple(trace))


def test_compiler_rejects_coordinate_only_target(
    valid_artifact_data: dict[str, Any],
) -> None:
    trace = list(complete_trace(valid_artifact_data))
    coordinate = LocatorBundle.model_validate(
        {
            "description": "Unstable coordinate",
            "candidates": [
                {
                    "strategy": "coordinates",
                    "x": 10,
                    "y": 20,
                    "width": 30,
                    "height": 40,
                    "viewport_width": 1280,
                    "viewport_height": 800,
                    "portability": "low",
                }
            ],
        }
    )
    trace[1] = recording(trace[1].action, coordinate, 1)

    with pytest.raises(CompilationError, match="coordinate-only"):
        compile_trace(tuple(trace))
