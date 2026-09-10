"""Application-service ports consumed by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from replayforge.runs.results import RunResult


class ReplayInvoker(Protocol):
    def ready(self) -> bool: ...

    def invoke(
        self,
        capability_id: str,
        version: str | None,
        tenant: str,
        inputs: dict[str, Any],
    ) -> RunResult: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    replay_invoker: ReplayInvoker
