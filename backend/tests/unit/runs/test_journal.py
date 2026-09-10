from __future__ import annotations

from datetime import UTC, datetime

import pytest

from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.runs.journal import InMemoryRunJournal
from replayforge.shared.clock import FrozenClock
from replayforge.shared.ids import EntityKind, new_id


def journal(redactor: StructuredRedactor | None = None) -> InMemoryRunJournal:
    return InMemoryRunJournal(
        str(new_id(EntityKind.RUN)),
        FrozenClock(datetime(2026, 9, 10, 12, tzinfo=UTC)),
        redactor or StructuredRedactor(),
    )


def test_journal_orders_events_and_exposes_opaque_manifest() -> None:
    recorder = journal()

    recorder.record("replay_started", recorder.run_id)
    recorder.record("action_result", recorder.run_id, step_id="search.submit")

    assert [event.sequence for event in recorder.events()] == [1, 2]
    assert recorder.events()[1].step_id == "search.submit"
    assert recorder.evidence_manifest_key == f"evidence://{recorder.run_id}/manifest.json"


def test_journal_drops_forbidden_detail_keys() -> None:
    recorder = journal()

    recorder.record(
        "policy_evaluated",
        recorder.run_id,
        details={"decision": "allow", "authorization_token": "must-not-survive"},
    )

    assert recorder.events()[0].details == {"decision": "allow"}


def test_journal_rejects_configured_secret_value() -> None:
    recorder = journal(StructuredRedactor(configured_secrets=("highly-sensitive",)))

    with pytest.raises(EvidenceRejectedError):
        recorder.record("unsafe_event", recorder.run_id, details={"note": "highly-sensitive"})


@pytest.mark.parametrize(
    ("event_type", "step_id"),
    [("Invalid Event", None), ("valid_event", "invalid step")],
)
def test_journal_validates_event_identity(event_type: str, step_id: str | None) -> None:
    recorder = journal()

    with pytest.raises(ValueError):
        recorder.record(event_type, recorder.run_id, step_id=step_id)


def test_journal_rejects_cross_run_event() -> None:
    recorder = journal()

    with pytest.raises(ValueError, match="different run"):
        recorder.record("replay_started", str(new_id(EntityKind.RUN)))
