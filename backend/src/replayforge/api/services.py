"""Application-service ports consumed by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from replayforge.discovery.models import DiscoveryResult
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


class DiscoveryInvoker(Protocol):
    def ready(self) -> bool: ...

    def invoke(
        self,
        *,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        inputs: dict[str, Any],
        max_steps: int,
        timeout_seconds: int,
    ) -> DiscoveryResult: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    replay_invoker: ReplayInvoker
    discovery_invoker: DiscoveryInvoker | None = None
