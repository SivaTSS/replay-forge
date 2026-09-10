"""Thread-safe structured run journal with redaction before retention."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from replayforge.evidence.redaction import StructuredRedactor
from replayforge.shared.clock import Clock
from replayforge.shared.ids import EntityId, EntityKind, new_id, parse_id

_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_STEP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class RunEvent:
    id: EntityId
    run_id: EntityId
    sequence: int
    event_type: str
    occurred_at: datetime
    step_id: str | None
    details: dict[str, object]


@dataclass(slots=True)
class InMemoryRunJournal:
    """Local adapter retaining only redacted, monotonically ordered events."""

    run_id: str
    clock: Clock
    redactor: StructuredRedactor = field(default_factory=StructuredRedactor)
    _events: list[RunEvent] = field(init=False, default_factory=list)
    _lock: Lock = field(init=False, default_factory=Lock)

    def __post_init__(self) -> None:
        parse_id(self.run_id, EntityKind.RUN)

    @property
    def evidence_manifest_key(self) -> str:
        return f"evidence://{self.run_id}/manifest.json"

    def record(
        self,
        event_type: str,
        run_id: str,
        *,
        step_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if run_id != self.run_id:
            raise ValueError("journal cannot record an event for a different run")
        if _EVENT_TYPE_PATTERN.fullmatch(event_type) is None:
            raise ValueError("event type must be a lowercase stable identifier")
        if step_id is not None and _STEP_ID_PATTERN.fullmatch(step_id) is None:
            raise ValueError("step ID is invalid")
        sanitized = self.redactor.sanitize_json(details or {}, {}, run_salt=self.run_id)
        cleaned = json.loads(sanitized.content)
        if not isinstance(cleaned, dict):
            raise RuntimeError("structured redactor must preserve mapping shape")
        with self._lock:
            event = RunEvent(
                id=new_id(EntityKind.EVENT),
                run_id=parse_id(run_id, EntityKind.RUN),
                sequence=len(self._events) + 1,
                event_type=event_type,
                occurred_at=self.clock.now(),
                step_id=step_id,
                details=cleaned,
            )
            self._events.append(event)

    def events(self) -> tuple[RunEvent, ...]:
        with self._lock:
            return tuple(self._events)
