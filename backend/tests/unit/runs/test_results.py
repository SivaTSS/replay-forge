from typing import cast

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from replayforge.runs.results import (
    CapabilityReference,
    RunResult,
    SuccessResult,
    VerifiedCheckpoint,
)

_RUN_ID = "run_0123456789abcdef0123456789abcdef"


def test_result_union_discriminates_success() -> None:
    result: RunResult = TypeAdapter(RunResult).validate_python(
        {
            "status": "success",
            "run_id": _RUN_ID,
            "capability": {"id": "member.lookup_savings_balance", "version": "1.0.0"},
            "outputs": {"available_balance": "1420.57"},
            "checkpoint": {"id": "savings_balance_verified", "verified": True},
            "evidence_manifest": f"evidence://{_RUN_ID}/manifest.json",
        }
    )

    assert isinstance(result, SuccessResult)
    assert result.checkpoint.verified is True


def test_success_cannot_claim_unverified_checkpoint() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RunResult).validate_python(
            {
                "status": "success",
                "run_id": _RUN_ID,
                "capability": {"id": "member.lookup", "version": "1.0.0"},
                "outputs": {},
                "checkpoint": {"id": "done", "verified": False},
                "evidence_manifest": f"evidence://{_RUN_ID}/manifest.json",
            }
        )


def test_unknown_terminal_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        TypeAdapter(RunResult).validate_python({"status": "maybe"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "run_example"),
        ("capability.id", "member lookup"),
        ("capability.version", "latest"),
        ("evidence_manifest", "https://example.test/manifest"),
        (
            "evidence_manifest",
            "evidence://run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/manifest.json",
        ),
    ],
)
def test_success_rejects_invalid_boundary_identifiers(field: str, value: str) -> None:
    payload = {
        "status": "success",
        "run_id": _RUN_ID,
        "capability": {"id": "member.lookup", "version": "1.0.0"},
        "outputs": {},
        "checkpoint": {"id": "done", "verified": True},
        "evidence_manifest": f"evidence://{_RUN_ID}/manifest.json",
    }
    target = payload
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]  # type: ignore[assignment]
    target[parts[-1]] = value

    with pytest.raises(ValidationError):
        TypeAdapter(RunResult).validate_python(payload)


def test_result_payload_must_be_json_safe() -> None:
    with pytest.raises(ValidationError):
        SuccessResult(
            status="success",
            run_id=_RUN_ID,
            capability=CapabilityReference(id="member.lookup", version="1.0.0"),
            outputs=cast(dict[str, JsonValue], {"unsafe": object()}),
            checkpoint=VerifiedCheckpoint(id="done", verified=True),
            evidence_manifest=f"evidence://{_RUN_ID}/manifest.json",
        )
