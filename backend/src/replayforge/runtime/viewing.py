"""Generic launch/catalog facade over the existing replay and discovery services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from replayforge.api.contracts import DiscoveryLaunch, LaunchRequest, ReplayLaunch
from replayforge.applications.registry import ApplicationRegistry
from replayforge.capabilities.registry import CapabilityRegistry
from replayforge.capabilities.values import validate_object
from replayforge.discovery.models import DiscoverySuccess
from replayforge.runs.discovery_suite import DiscoverySuiteError, DiscoverySuiteService
from replayforge.runs.service import ReplayApplicationService
from replayforge.runs.viewing import ExecutionViewer, ViewingError, current_execution
from replayforge.shared.yaml import load_unique_yaml


@dataclass(frozen=True, slots=True)
class ExecutionController:
    viewer: ExecutionViewer
    registry: CapabilityRegistry
    applications: ApplicationRegistry
    replay: ReplayApplicationService
    suites: DiscoverySuiteService
    presets_file: Path | None = None

    def catalog(self) -> dict[str, Any]:
        presets: list[dict[str, Any]] = []
        if self.presets_file is not None and self.presets_file.is_file():
            raw = load_unique_yaml(self.presets_file.read_text())
            if not isinstance(raw, dict) or not isinstance(raw.get("workflows"), dict):
                raise ViewingError("presets_invalid", 503)
            for name, item in raw["workflows"].items():
                launch = DiscoveryLaunch.model_validate(
                    {
                        "mode": "discovery",
                        **{
                            key: item[key]
                            for key in (
                                "goal",
                                "application_family",
                                "tenant",
                                "entry_point",
                                "inputs",
                                "validation_tenants",
                            )
                            if key in item
                        },
                    }
                )
                presets.append(
                    {
                        "name": name,
                        "capability_id": item.get("expected_capability_id"),
                        "discovery": launch.model_dump(mode="json"),
                    }
                )
        return {
            "capabilities": [
                {
                    "id": record.artifact.capability.id,
                    "version": record.artifact.capability.version,
                    "name": record.artifact.capability.name,
                    "risk": record.artifact.capability.risk.value,
                    "tenants": list(record.artifact.compatibility.supported_variants),
                    "inputs": record.artifact.inputs.model_dump(mode="json"),
                }
                for record in self.registry.all()
            ],
            "applications": [
                {
                    "id": application.application_family,
                    "tenants": list(application.tenants),
                    "entry_points": list(application.entry_points),
                }
                for application in self.applications.all()
            ],
            "presets": presets,
            "discovery_ready": self.suites.ready(),
        }

    def start(self, request: LaunchRequest) -> dict[str, str]:
        launch = request.execution
        if isinstance(launch, ReplayLaunch):
            record = (
                self.registry.get(launch.capability_id, launch.version)
                if launch.version
                else self.registry.latest(launch.capability_id)
            )
            validate_object(record.artifact.inputs, launch.inputs)
            self.applications.resolve(
                record.artifact.capability.application_family,
                launch.tenant,
                record.artifact.compatibility.entry_point,
            )

            def replay() -> dict[str, Any]:
                feed = current_execution.get()
                assert feed is not None
                feed.phase("replay")
                return self.replay.invoke(
                    launch.capability_id,
                    record.artifact.capability.version,
                    launch.tenant,
                    launch.inputs,
                ).model_dump(mode="json")

            return self.viewer.start("replay", replay)
        if not self.suites.ready():
            raise ViewingError("discovery_not_ready", 503)
        tenants = tuple(dict.fromkeys((launch.tenant, *launch.validation_tenants)))
        for tenant in tenants:
            self.applications.resolve(launch.application_family, tenant, launch.entry_point)

        def discover() -> dict[str, Any]:
            feed = current_execution.get()
            assert feed is not None
            feed.phase("discovery")
            suite = self.suites.create(**launch.model_dump(exclude={"mode", "validation_tenants"}))
            if not isinstance(suite.primary, DiscoverySuccess):
                return suite.primary.model_dump(mode="json")
            try:
                for tenant in tenants:
                    feed.phase(f"validation:{tenant}")
                    self.suites.validate(suite.suite_id, tenant=tenant, inputs=launch.inputs)
                feed.phase("publication")
                suite = self.suites.finalize(suite.suite_id)
            except DiscoverySuiteError:
                return {
                    "status": "failure",
                    "code": "discovery_validation_failed",
                    "suite_id": suite.suite_id,
                    "run_id": suite.primary.run_id,
                }
            artifact = self.suites.published_artifact(suite.suite_id)
            assert isinstance(suite.primary, DiscoverySuccess)
            return {
                "status": "success",
                "suite_id": suite.suite_id,
                "run_id": suite.primary.run_id,
                "capability": {
                    "id": artifact.capability.id,
                    "version": artifact.capability.version,
                },
                "evidence_manifest": suite.primary.evidence_manifest,
            }

        return self.viewer.start("discovery", discover)
