import pytest
from pydantic import TypeAdapter, ValidationError

from replayforge.runs.results import RunResult, SuccessResult


def test_result_union_discriminates_success() -> None:
    result: RunResult = TypeAdapter(RunResult).validate_python(
        {
            "status": "success",
            "run_id": "run_example",
            "capability": {"id": "member.lookup_savings_balance", "version": "1.0.0"},
            "outputs": {"available_balance": "1420.57"},
            "checkpoint": {"id": "savings_balance_verified", "verified": True},
            "evidence_manifest": "evidence://run/manifest.json",
        }
    )

    assert isinstance(result, SuccessResult)
    assert result.checkpoint.verified is True


def test_success_cannot_claim_unverified_checkpoint() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RunResult).validate_python(
            {
                "status": "success",
                "run_id": "run_example",
                "capability": {"id": "member.lookup", "version": "1.0.0"},
                "outputs": {},
                "checkpoint": {"id": "done", "verified": False},
                "evidence_manifest": "evidence://run/manifest.json",
            }
        )


def test_unknown_terminal_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        TypeAdapter(RunResult).validate_python({"status": "maybe"})
