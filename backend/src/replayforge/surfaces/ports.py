"""Ports separating capability semantics from concrete UI technology."""

from __future__ import annotations

from typing import Any, Protocol

from replayforge.capabilities.models import Action, Condition, Landmark, LocatorBundle
from replayforge.surfaces.models import (
    ActionReceipt,
    NormalizedObservation,
    ResolvedTarget,
    SanitizedSurfaceFrame,
)


class SurfaceSession(Protocol):
    @property
    def session_id(self) -> str: ...

    @property
    def origin(self) -> str: ...

    @property
    def surface_contract(self) -> str: ...

    @property
    def base_variant(self) -> str: ...

    @property
    def rendered_surface(self) -> bool: ...

    @property
    def required_landmarks(self) -> tuple[Landmark, ...]: ...

    @property
    def forbidden_landmarks(self) -> tuple[Landmark, ...]: ...

    def observe(self) -> NormalizedObservation: ...

    def capture_provider_frame(self) -> bytes: ...

    def capture_sanitized_evidence_frame(self) -> SanitizedSurfaceFrame: ...

    def resolve(self, target: LocatorBundle, timeout_ms: int) -> ResolvedTarget: ...

    def capture_locator(self, target: ResolvedTarget) -> LocatorBundle: ...

    def execute(
        self, action: Action, target: ResolvedTarget | None, inputs: dict[str, Any]
    ) -> ActionReceipt: ...

    def evaluate(
        self,
        condition: Condition,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
    ) -> bool: ...

    def wait_until(
        self,
        condition: Condition,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> bool: ...

    def extract(self, target: ResolvedTarget) -> str: ...

    def close(self) -> None: ...


class SurfaceDriver(Protocol):
    def open(self, application_family: str, tenant: str, entry_point: str) -> SurfaceSession: ...
