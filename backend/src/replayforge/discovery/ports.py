"""Ports used by discovery without provider or compiler coupling."""

from __future__ import annotations

from typing import Protocol

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.discovery.models import (
    DiscoveryProposal,
    ProviderContext,
    RecordedDiscoveryStep,
)
from replayforge.surfaces.models import NormalizedObservation


class ModelProviderError(RuntimeError):
    """A provider failed without exposing raw provider content to callers."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class ModelProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def decide(self, context: ProviderContext) -> DiscoveryProposal: ...


class ArtifactCompiler(Protocol):
    @property
    def required_output_names(self) -> tuple[str, ...]: ...

    def compile(
        self,
        *,
        run_id: str,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        steps: tuple[RecordedDiscoveryStep, ...],
        final_observation: NormalizedObservation,
        provider_name: str,
        model_name: str,
        evidence_manifest: str,
    ) -> CapabilityArtifact: ...
