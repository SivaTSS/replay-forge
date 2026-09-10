"""Ports separating capability semantics from concrete UI technology."""

from __future__ import annotations

from typing import Any, Protocol

from replayforge.capabilities.models import Action, Condition, LocatorBundle
from replayforge.surfaces.models import ActionReceipt, NormalizedObservation, ResolvedTarget


class SurfaceSession(Protocol):
    @property
    def session_id(self) -> str: ...

    def observe(self) -> NormalizedObservation: ...

    def resolve(self, target: LocatorBundle, timeout_ms: int) -> ResolvedTarget: ...

    def execute(
        self, action: Action, target: ResolvedTarget | None, inputs: dict[str, Any]
    ) -> ActionReceipt: ...

    def evaluate(self, condition: Condition, outputs: dict[str, Any]) -> bool: ...

    def extract(self, target: ResolvedTarget) -> str: ...

    def close(self) -> None: ...


class SurfaceDriver(Protocol):
    def open(self, application_family: str, tenant: str, entry_point: str) -> SurfaceSession: ...
