"""Model-free reuse of the three genuine workstation discoveries."""

from pathlib import Path

import pytest

from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.runs.results import SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock

pytestmark = pytest.mark.integration
REPOSITORY = Path(__file__).resolve().parents[3]

CASES = [
    (
        "member.transaction_investigation",
        "1.0.1",
        {"member_id": "12346", "account_id": "12346-01", "transaction_reference": "POS-80429"},
        {
            "transaction_reference": "POS-80429",
            "account_id": "12346-01",
            "amount": "-$84.27",
            "posted_date": "2026-09-08",
            "description": "NORTHWIND MARKET / POS PURCHASE",
            "posting_status": "Posted",
        },
    ),
    (
        "member.servicing_loan_payoff_quote",
        "1.0.1",
        {"member_id": "12346", "payoff_date": "2026-09-21"},
        {"payoff_amount": "$9,035.70", "good_through_date": "2026-09-21"},
    ),
    (
        "member.temporary_card_lock",
        "1.0.1",
        {
            "member_id": "12346",
            "card_id": "12346-D1",
            "reason": "Synthetic alternate-member replay",
        },
        {"card_id": "12346-D1", "lock_status": "Temporarily locked"},
    ),
]


@pytest.mark.parametrize("tenant", ["harbor", "summit"])
@pytest.mark.parametrize(
    "capability_id,version,inputs,expected", CASES, ids=["transaction", "payoff", "card-lock"]
)
def test_genuine_artifacts_reuse_changed_inputs_without_a_model(
    demo_bank: str,
    tmp_path: Path,
    tenant: str,
    capability_id: str,
    version: str,
    inputs: dict[str, str],
    expected: dict[str, str],
) -> None:
    """Different member and inputs, both tenants, larger layout; no provider credentials."""
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=REPOSITORY / "capabilities",
            evidence_directory=tmp_path / "evidence",
            demo_base_url=demo_bank,
            browser_viewport_width=1440,
            browser_viewport_height=900,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    try:
        result = runtime.service.invoke(capability_id, version, tenant, inputs)
        assert isinstance(result, SuccessResult), result
        expected_outputs = dict(expected)
        if capability_id != "member.transaction_investigation":
            expected_outputs["confirmation_reference"] = (
                "HBR-000001" if tenant == "harbor" else "SUM-000001"
            )
        assert result.outputs == expected_outputs
        assert result.checkpoint.verified
        verified = verify_run_manifest(
            LocalEvidenceStore(tmp_path / "evidence", SystemClock()), result.evidence_manifest
        )
        assert verified.terminal_result_verified
        assert not any(
            event.event_type.startswith("model_")
            for event in runtime.journals[result.run_id].events()
        )
    finally:
        runtime.close()
