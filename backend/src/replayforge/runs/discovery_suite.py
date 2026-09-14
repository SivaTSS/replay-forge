"""Draft-oriented discovery suites and publication gates."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from threading import Lock
from typing import Any, Literal, Protocol

from replayforge.applications.registry import ApplicationRegistry
from replayforge.capabilities.models import (
    ApplicationFailure,
    AssertAction,
    BusinessOutcome,
    CapabilityArtifact,
    Condition,
    OutcomeResult,
    Recovery,
)
from replayforge.capabilities.registry import (
    CapabilityConflictError,
    CapabilityIntegrityError,
    CapabilityNotFoundError,
    CapabilityPublicationError,
    CapabilityRegistry,
)
from replayforge.capabilities.serialization import artifact_content_hash
from replayforge.discovery.models import DiscoveryResult, DiscoverySuccess, ScenarioContext
from replayforge.policy.types import RISK_RANK, Risk
from replayforge.runs.discovery_service import DiscoveryApplicationService
from replayforge.runs.results import FailureResult, InterventionRequiredResult, RunResult
from replayforge.shared.ids import EntityKind, new_id, parse_id

ScenarioKind = Literal["business_outcome", "application_failure", "recovery"]


@dataclass(frozen=True, slots=True)
class ReplayValidation:
    """Actual replay disposition plus observed, completed recovery identities."""

    result: RunResult
    completed_recoveries: tuple[str, ...] = ()

    def verifies(self, scenario: DiscoveryScenario | None = None) -> bool:
        if scenario is None:
            return self.result.status == "success"
        if scenario.kind == "recovery":
            return self.result.status == "success" and scenario.code in self.completed_recoveries
        expected = "business_outcome" if scenario.kind == "business_outcome" else "failure"
        return (
            self.result.status == expected and getattr(self.result, "code", None) == scenario.code
        )


class DiscoverySuiteStatus(StrEnum):
    COLLECTING = "collecting"
    VALIDATED = "validated"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CompatibilityValidationFailure:
    tenant: str
    reason_code: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.tenant) is None:
            raise ValueError("validation failure tenant is invalid")
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.reason_code) is None:
            raise ValueError("validation failure reason code is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryScenario:
    kind: ScenarioKind
    goal: str
    code: str
    description: str
    result: DiscoveryResult
    inputs: dict[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.code) is None:
            raise ValueError("discovery scenario code is invalid")
        if not self.goal.strip() or not self.description.strip():
            raise ValueError("discovery scenario requires a goal and description")


@dataclass(frozen=True, slots=True)
class DiscoverySuite:
    suite_id: str
    goal: str
    application_family: str
    tenant: str
    entry_point: str
    primary_inputs: dict[str, Any]
    primary: DiscoveryResult
    scenarios: tuple[DiscoveryScenario, ...] = ()
    artifact: CapabilityArtifact | None = None
    status: DiscoverySuiteStatus = DiscoverySuiteStatus.COLLECTING
    published_version: str | None = None
    validation_failures: tuple[CompatibilityValidationFailure, ...] = ()
    primary_source: Literal["new_discovery", "published_capability"] = "new_discovery"

    def __post_init__(self) -> None:
        parse_id(self.suite_id, EntityKind.SUITE)
        if not isinstance(self.status, DiscoverySuiteStatus):
            raise ValueError("discovery suite status must use the domain enum")
        if self.primary_source not in {"new_discovery", "published_capability"}:
            raise ValueError("discovery primary source is invalid")
        if not self.goal.strip():
            raise ValueError("discovery suite goal cannot be empty")
        identifiers = (
            (self.application_family, r"[a-z][a-z0-9_]{1,63}"),
            (self.tenant, r"[a-z][a-z0-9_-]{1,63}"),
            (self.entry_point, r"[a-z][a-z0-9_]{1,63}"),
        )
        if any(re.fullmatch(pattern, value) is None for value, pattern in identifiers):
            raise ValueError("discovery suite routing identity is invalid")
        if len({scenario.code for scenario in self.scenarios}) != len(self.scenarios):
            raise ValueError("discovery scenario codes must be unique")
        primary_succeeded = isinstance(self.primary, DiscoverySuccess)
        if self.scenarios and not primary_succeeded:
            raise ValueError("discovery scenarios require a successful primary run")
        if (
            self.status
            in {
                DiscoverySuiteStatus.COLLECTING,
                DiscoverySuiteStatus.VALIDATED,
                DiscoverySuiteStatus.PUBLISHED,
            }
            and not primary_succeeded
        ):
            raise ValueError("active discovery suite requires a successful primary run")
        if (
            self.status
            in {
                DiscoverySuiteStatus.VALIDATED,
                DiscoverySuiteStatus.PUBLISHED,
            }
            and self.artifact is None
        ):
            raise ValueError("validated and published suites require an artifact")
        if self.status is DiscoverySuiteStatus.PUBLISHED:
            assert self.artifact is not None
            if self.published_version != self.artifact.capability.version:
                raise ValueError("published suite version must match its artifact")
        elif self.published_version is not None:
            raise ValueError("only a published suite may declare a published version")
        draft = self.primary.artifact if isinstance(self.primary, DiscoverySuccess) else None
        candidate = self.artifact or draft
        if (
            candidate is not None
            and candidate.capability.application_family != self.application_family
        ):
            raise ValueError("suite artifact belongs to a different application family")

    def snapshot(self) -> dict[str, object]:
        primary = self.primary
        draft = primary.artifact if isinstance(primary, DiscoverySuccess) else None
        snapshot_artifact = self.artifact or draft
        artifact_snapshot = (
            _artifact_snapshot(snapshot_artifact) if snapshot_artifact is not None else None
        )
        return {
            "suite_id": self.suite_id,
            "status": self.status.value,
            "application_family": self.application_family,
            "tenant": self.tenant,
            "entry_point": self.entry_point,
            "primary": _result_snapshot(primary),
            "primary_source": self.primary_source,
            "scenario_count": len(self.scenarios),
            "scenarios": [
                {
                    "kind": scenario.kind,
                    "code": scenario.code,
                    "result": _result_snapshot(scenario.result),
                }
                for scenario in self.scenarios
            ],
            "artifact": artifact_snapshot,
            "published_version": self.published_version,
            "validation_failures": [
                {"tenant": failure.tenant, "reason": failure.reason_code}
                for failure in self.validation_failures
            ],
        }


class DiscoverySuiteError(ValueError):
    """A suite operation cannot be safely completed."""


class DiscoverySuiteRepository(Protocol):
    def get(self, suite_id: str) -> DiscoverySuite: ...

    def save(self, suite: DiscoverySuite) -> None: ...


@dataclass(slots=True)
class InMemoryDiscoverySuiteRepository:
    _records: dict[str, DiscoverySuite] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def get(self, suite_id: str) -> DiscoverySuite:
        with self._lock:
            try:
                return deepcopy(self._records[suite_id])
            except KeyError as exc:
                raise DiscoverySuiteError("discovery suite was not found") from exc

    def save(self, suite: DiscoverySuite) -> None:
        with self._lock:
            self._records[suite.suite_id] = deepcopy(suite)


@dataclass(slots=True)
class DiscoverySuiteService:
    discovery: DiscoveryApplicationService
    registry: CapabilityRegistry
    validator: Callable[[CapabilityArtifact, str, dict[str, Any]], ReplayValidation] | None = None
    application_registry: ApplicationRegistry | None = None
    repository: DiscoverySuiteRepository | None = None

    def __post_init__(self) -> None:
        if self.repository is None:
            self.repository = InMemoryDiscoverySuiteRepository()

    @property
    def _store(self) -> DiscoverySuiteRepository:
        assert self.repository is not None
        return self.repository

    def ready(self) -> bool:
        return (
            self.validator is not None
            and self.discovery.ready()
            and (self.application_registry is None or self.application_registry.ready())
        )

    def create(
        self,
        *,
        goal: str,
        application_family: str,
        tenant: str,
        entry_point: str,
        inputs: dict[str, Any],
        max_steps: int,
        timeout_seconds: int,
        existing_capability_id: str | None = None,
    ) -> DiscoverySuite:
        self._assert_application_target(application_family, tenant, entry_point)
        result = self.discovery.discover(
            goal=goal,
            application_family=application_family,
            tenant=tenant,
            entry_point=entry_point,
            inputs=inputs,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            existing_capability_id=existing_capability_id,
        )
        suite = DiscoverySuite(
            suite_id=str(new_id(EntityKind.SUITE)),
            goal=goal,
            application_family=application_family,
            tenant=tenant,
            entry_point=entry_point,
            primary_inputs=dict(inputs),
            primary=result,
            status=(
                DiscoverySuiteStatus.COLLECTING
                if isinstance(result, DiscoverySuccess)
                else DiscoverySuiteStatus.FAILED
            ),
        )
        self._store.save(suite)
        return suite

    def from_published(
        self, *, capability_id: str, version: str, tenant: str, inputs: dict[str, Any]
    ) -> DiscoverySuite:
        """Extend an immutable learned trace; do not claim a new primary discovery ran."""
        try:
            artifact = self.registry.get(capability_id, version).artifact
        except (
            CapabilityNotFoundError,
            CapabilityIntegrityError,
            CapabilityPublicationError,
        ) as error:
            raise DiscoverySuiteError("published primary is unavailable") from error
        family = artifact.capability.application_family
        entry_point = artifact.compatibility.entry_point
        self._assert_application_target(family, tenant, entry_point)
        if self.validator is None or not self.validator(artifact, tenant, inputs).verifies():
            raise DiscoverySuiteError("published primary did not pass fresh deterministic replay")
        primary = DiscoverySuccess(
            status="success",
            run_id=artifact.provenance.discovery_run_id,
            artifact=artifact,
            evidence_manifest=artifact.provenance.evidence_manifest_key,
        )
        suite = DiscoverySuite(
            suite_id=str(new_id(EntityKind.SUITE)),
            goal=artifact.capability.description,
            application_family=family,
            tenant=tenant,
            entry_point=entry_point,
            primary_inputs=deepcopy(inputs),
            primary=primary,
            primary_source="published_capability",
        )
        self._store.save(suite)
        return suite

    def get(self, suite_id: str) -> DiscoverySuite:
        return self._store.get(suite_id)

    def add_scenario(
        self,
        suite_id: str,
        *,
        kind: ScenarioKind,
        goal: str,
        inputs: dict[str, Any],
        code: str | None,
        description: str | None,
        max_steps: int,
        timeout_seconds: int,
    ) -> DiscoverySuite:
        suite = self.get(suite_id)
        if kind not in {"business_outcome", "application_failure", "recovery"}:
            raise DiscoverySuiteError("scenario kind is not supported")
        if not isinstance(suite.primary, DiscoverySuccess):
            raise DiscoverySuiteError("a scenario requires a successful primary discovery")
        if suite.status not in {
            DiscoverySuiteStatus.COLLECTING,
            DiscoverySuiteStatus.VALIDATED,
        }:
            raise DiscoverySuiteError("suite is no longer collecting scenarios")
        scenario_code = code or f"scenario_{len(suite.scenarios) + 1}"
        if any(existing.code == scenario_code for existing in suite.scenarios):
            raise DiscoverySuiteError("scenario codes must be unique within a suite")
        result = self.discovery.discover(
            goal=goal,
            application_family=suite.application_family,
            tenant=suite.tenant,
            entry_point=suite.entry_point,
            inputs=inputs,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            scenario=ScenarioContext(suite.primary.artifact, kind),
        )
        scenario = DiscoveryScenario(
            kind=kind,
            goal=goal,
            code=scenario_code,
            description=description or goal,
            result=result,
            inputs=deepcopy(inputs),
        )
        if any(existing.code == scenario.code for existing in suite.scenarios):
            raise DiscoverySuiteError("scenario codes must be unique within a suite")
        updated = replace(
            suite,
            scenarios=(*suite.scenarios, scenario),
            artifact=None,
            status=DiscoverySuiteStatus.COLLECTING,
        )
        self._store.save(updated)
        return updated

    def finalize(self, suite_id: str) -> DiscoverySuite:
        suite = self.get(suite_id)
        if suite.status not in {
            DiscoverySuiteStatus.COLLECTING,
            DiscoverySuiteStatus.VALIDATED,
        }:
            raise DiscoverySuiteError("suite is not ready for finalization")
        if not isinstance(suite.primary, DiscoverySuccess):
            raise DiscoverySuiteError("cannot finalize an unsuccessful primary discovery")
        try:
            artifact = suite.artifact or _merge_scenarios(suite.primary.artifact, suite.scenarios)
        except ValueError as error:
            raise DiscoverySuiteError("scenario evidence could not be merged safely") from error
        if artifact.capability.risk in {Risk.SENSITIVE, Risk.IRREVERSIBLE}:
            updated = replace(suite, artifact=artifact, status=DiscoverySuiteStatus.FAILED)
            self._store.save(updated)
            raise DiscoverySuiteError(
                "sensitive and irreversible capability drafts cannot be published"
            )
        if self.validator is None:
            raise DiscoverySuiteError("final deterministic replay validation is unavailable")
        if not self.validator(artifact, suite.tenant, suite.primary_inputs).verifies():
            self._record_validation_failure(suite, suite.tenant, "primary_replay_failed")
            raise DiscoverySuiteError("final deterministic replay did not pass")
        self._validate_scenarios(suite, artifact, suite.tenant)
        try:
            published = self.registry.publish_next(artifact)
        except (
            CapabilityConflictError,
            CapabilityIntegrityError,
            CapabilityPublicationError,
        ) as error:
            updated = replace(
                suite,
                artifact=artifact,
                status=DiscoverySuiteStatus.VALIDATED,
            )
            self._store.save(updated)
            raise DiscoverySuiteError("capability publication failed safely") from error
        artifact = published.artifact
        updated = replace(
            suite,
            artifact=artifact,
            status=DiscoverySuiteStatus.PUBLISHED,
            published_version=artifact.capability.version,
        )
        self._store.save(updated)
        return updated

    def validate(self, suite_id: str, *, tenant: str, inputs: dict[str, Any]) -> DiscoverySuite:
        suite = self.get(suite_id)
        if suite.status not in {
            DiscoverySuiteStatus.COLLECTING,
            DiscoverySuiteStatus.VALIDATED,
        }:
            raise DiscoverySuiteError("suite is not accepting compatibility validations")
        if not isinstance(suite.primary, DiscoverySuccess):
            raise DiscoverySuiteError("a scenario requires a successful primary discovery")
        self._assert_application_target(suite.application_family, tenant, suite.entry_point)
        try:
            artifact = suite.artifact or _merge_scenarios(suite.primary.artifact, suite.scenarios)
        except ValueError as error:
            raise DiscoverySuiteError("scenario evidence could not be merged safely") from error
        if self.validator is None:
            raise DiscoverySuiteError("deterministic compatibility validation is unavailable")
        if not self.validator(artifact, tenant, inputs).verifies():
            self._record_validation_failure(suite, tenant, "compatibility_replay_failed")
            raise DiscoverySuiteError("compatibility replay did not pass")
        self._validate_scenarios(suite, artifact, tenant)
        if tenant not in artifact.compatibility.supported_variants:
            compatibility = artifact.compatibility.model_copy(
                update={
                    "supported_variants": (
                        *artifact.compatibility.supported_variants,
                        tenant,
                    )
                }
            )
            artifact = artifact.model_copy(
                update={
                    "compatibility": compatibility,
                    "provenance": artifact.provenance.model_copy(
                        update={"artifact_content_hash": None}
                    ),
                }
            )
            artifact = CapabilityArtifact.model_validate(artifact.model_dump(mode="python"))
        updated = replace(suite, artifact=artifact, status=DiscoverySuiteStatus.VALIDATED)
        self._store.save(updated)
        return updated

    def _validate_scenarios(
        self, suite: DiscoverySuite, artifact: CapabilityArtifact, tenant: str
    ) -> None:
        assert self.validator is not None
        for scenario in suite.scenarios:
            proof = self.validator(artifact, tenant, deepcopy(scenario.inputs))
            if not proof.verifies(scenario):
                self._record_validation_failure(suite, tenant, "scenario_replay_failed")
                raise DiscoverySuiteError("scenario replay did not verify its declared disposition")

    def _assert_application_target(
        self, application_family: str, tenant: str, entry_point: str
    ) -> None:
        if self.application_registry is None:
            return
        try:
            self.application_registry.resolve(application_family, tenant, entry_point)
        except ValueError as error:
            raise DiscoverySuiteError("application target is not registered") from error

    def _record_validation_failure(
        self, suite: DiscoverySuite, tenant: str, reason_code: str
    ) -> None:
        updated = replace(
            suite,
            validation_failures=(
                *suite.validation_failures,
                CompatibilityValidationFailure(tenant, reason_code),
            ),
        )
        self._store.save(updated)

    def published_artifact(self, suite_id: str) -> CapabilityArtifact:
        suite = self.get(suite_id)
        if suite.status is not DiscoverySuiteStatus.PUBLISHED or suite.artifact is None:
            raise DiscoverySuiteError("suite artifact is not published")
        return suite.artifact

    def scenario_artifact(self, suite_id: str, code: str) -> CapabilityArtifact:
        suite = self.get(suite_id)
        for scenario in suite.scenarios:
            if scenario.code == code and isinstance(scenario.result, DiscoverySuccess):
                return scenario.result.artifact
        raise DiscoverySuiteError("verified scenario artifact was not found")


def _merge_scenarios(
    artifact: CapabilityArtifact, scenarios: tuple[DiscoveryScenario, ...]
) -> CapabilityArtifact:
    outcomes = list(artifact.outcomes)
    failures = list(artifact.failures)
    recoveries = list(artifact.recoveries)
    steps = list(artifact.steps)
    for scenario in scenarios:
        if not isinstance(scenario.result, DiscoverySuccess):
            raise ValueError(f"scenario {scenario.code} did not produce verified evidence")
        observed = scenario.result.branch
        if observed is None:
            raise ValueError("scenario trace lacks an explicit verified branch marker")
        prefix_length = _shared_prefix_length(artifact, scenario.result.artifact)
        if prefix_length < observed.after_step_count:
            raise ValueError("scenario trace does not share a verified primary prefix")
        prefix_length = observed.after_step_count
        branch_index = prefix_length - 1
        branch_step = steps[branch_index]
        scenario_artifact = scenario.result.artifact
        if prefix_length >= len(scenario_artifact.steps):
            raise ValueError("scenario branch marker is absent from its executed trace")
        marker = scenario_artifact.steps[prefix_length]
        if (
            not isinstance(marker.action, AssertAction)
            or marker.action.condition != observed.condition
        ):
            raise ValueError("scenario branch marker does not match its executed assertion")
        condition = observed.condition
        if condition.kind not in {"text", "rendered_text", "visual_text", "element"}:
            raise ValueError("branch requires a positive distinctive surface condition")
        if scenario.kind != "recovery" and len(scenario_artifact.steps) != prefix_length + 1:
            raise ValueError("terminal scenario must stop after its verified branch marker")
        primary_surface_keys = {
            _condition_key(observed)
            for postcondition in branch_step.postconditions
            for observed in _surface_conditions(postcondition)
        }
        if scenario.kind == "business_outcome":
            if _condition_key(condition) in primary_surface_keys:
                raise ValueError("business outcome does not establish a distinct branch")
            outcomes.append(
                BusinessOutcome(
                    code=scenario.code,
                    description=scenario.description,
                    detect=condition,
                    allowed_after_steps=(branch_step.id,),
                    result=OutcomeResult(),
                )
            )
            steps[branch_index] = branch_step.model_copy(
                update={"outcome_refs": (*branch_step.outcome_refs, scenario.code)}
            )
        elif scenario.kind == "application_failure":
            if _condition_key(condition) in primary_surface_keys:
                raise ValueError("application failure does not establish a distinct branch")
            failures.append(
                ApplicationFailure(
                    code=scenario.code,
                    description=scenario.description,
                    detect=condition,
                    allowed_after_steps=(branch_step.id,),
                    expected_state="the requested operation is unavailable",
                    observed_state=scenario.description,
                    recoverable=False,
                )
            )
            steps[branch_index] = branch_step.model_copy(
                update={"failure_refs": (*branch_step.failure_refs, scenario.code)}
            )
        elif scenario.kind == "recovery":
            if prefix_length >= len(steps):
                raise ValueError("recovery trace has no primary state to rejoin")
            recovery_source_steps = scenario_artifact.steps[prefix_length + 1 :]
            if not recovery_source_steps:
                raise ValueError("recovery trace contains no recovery actions")
            if any(
                RISK_RANK[step.risk] >= RISK_RANK[Risk.SENSITIVE] for step in recovery_source_steps
            ):
                raise ValueError("recovery traces must remain read-only or reversible")
            recovered_surface_keys = {
                _condition_key(observed)
                for observed in _surface_conditions(scenario_artifact.checkpoint.condition)
            }
            if not recovered_surface_keys - {_condition_key(condition)}:
                raise ValueError("recovery trace lacks a distinct verified restored state")
            recovery_steps = tuple(
                step.model_copy(
                    update={
                        "id": f"recovery_{scenario.code}_{index}",
                        "recovery_refs": (),
                        "outcome_refs": (),
                        "failure_refs": (),
                    }
                )
                for index, step in enumerate(recovery_source_steps, start=1)
            )
            recoveries.append(
                Recovery(
                    id=scenario.code,
                    trigger=condition,
                    max_uses=1,
                    steps=recovery_steps,
                    resume_at=steps[prefix_length].id,
                )
            )
            steps[branch_index] = branch_step.model_copy(
                update={"recovery_refs": (*branch_step.recovery_refs, scenario.code)}
            )
    if not outcomes and not failures and not recoveries:
        return artifact
    merged = artifact.model_copy(
        update={
            "outcomes": tuple(outcomes),
            "failures": tuple(failures),
            "recoveries": tuple(recoveries),
            "steps": tuple(steps),
            "policy": artifact.policy.model_copy(
                update={
                    "allowed_action_types": artifact.policy.allowed_action_types
                    | frozenset(
                        step.action.kind for recovery in recoveries for step in recovery.steps
                    ),
                    "allowed_route_patterns": artifact.policy.allowed_route_patterns
                    | frozenset(
                        route
                        for scenario in scenarios
                        if isinstance(scenario.result, DiscoverySuccess)
                        for route in scenario.result.artifact.policy.allowed_route_patterns
                    ),
                }
            ),
            "provenance": artifact.provenance.model_copy(update={"artifact_content_hash": None}),
        }
    )
    return CapabilityArtifact.model_validate(merged.model_dump(mode="python"))


def _shared_prefix_length(primary: CapabilityArtifact, scenario: CapabilityArtifact) -> int:
    length = 0
    for primary_step, scenario_step in zip(primary.steps, scenario.steps, strict=False):
        if primary_step.action.model_dump(mode="json") != scenario_step.action.model_dump(
            mode="json"
        ):
            break
        # Two clicks are not the same operation when they address different controls.
        # Descriptive prose is not identity; scope, candidate order and state constraints are.
        primary_target = (
            primary_step.target.model_dump(mode="json", exclude={"description"})
            if primary_step.target is not None
            else None
        )
        scenario_target = (
            scenario_step.target.model_dump(mode="json", exclude={"description"})
            if scenario_step.target is not None
            else None
        )
        if primary_target != scenario_target or primary_step.risk != scenario_step.risk:
            break
        length += 1
    return length


def _surface_conditions(condition: Condition) -> tuple[Condition, ...]:
    if getattr(condition, "kind", None) in {
        "route",
        "text",
        "rendered_text",
        "visual_text",
        "element",
    }:
        return (condition,)
    nested = getattr(condition, "conditions", None)
    if nested is not None:
        return tuple(observed for item in nested for observed in _surface_conditions(item))
    child = getattr(condition, "condition", None)
    if child is not None and _surface_conditions(child):
        return (condition,)
    return ()


def _condition_key(condition: Condition) -> str:
    return json.dumps(condition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _result_snapshot(result: DiscoveryResult) -> dict[str, object]:
    if isinstance(result, DiscoverySuccess):
        return {
            "status": result.status,
            "run_id": result.run_id,
            "capability_id": result.artifact.capability.id,
            "content_hash": artifact_content_hash(result.artifact),
            "evidence_manifest": result.evidence_manifest,
        }
    if isinstance(result, FailureResult | InterventionRequiredResult):
        snapshot: dict[str, object] = {
            "status": result.status,
            "run_id": result.run_id,
            "code": result.code,
        }
        if result.step_id is not None:
            snapshot["step_id"] = result.step_id
        if isinstance(result, FailureResult):
            snapshot["recoverable"] = result.recoverable
            snapshot["evidence_manifest"] = result.evidence_manifest
            if result.privacy_rejection is not None:
                snapshot["privacy_rejection"] = result.privacy_rejection.model_dump(mode="json")
        else:
            snapshot["intervention_id"] = result.intervention_id
            snapshot["session_live"] = result.session_live
            snapshot["control_owner"] = result.control_owner
        return snapshot
    return {"status": "unknown"}


def _artifact_snapshot(artifact: CapabilityArtifact) -> dict[str, object]:
    """Expose review metadata without returning schemas, examples, or observed values."""
    return {
        "capability_id": artifact.capability.id,
        "version": artifact.capability.version,
        "risk": artifact.capability.risk.value,
        "content_hash": artifact_content_hash(artifact),
        "input_fields": list(artifact.inputs.properties),
        "output_fields": list(artifact.outputs.required),
        "input_contract": _contract_snapshot(artifact.inputs),
        "output_contract": _contract_snapshot(artifact.outputs),
        "supported_variants": list(artifact.compatibility.supported_variants),
        "step_count": len(artifact.steps),
        "route_patterns": sorted(artifact.policy.allowed_route_patterns),
    }


def _contract_snapshot(contract: Any) -> dict[str, object]:
    return {
        "required": list(contract.required),
        "properties": {
            name: {
                "type": schema.type.value,
                "data_classification": schema.data_classification.value,
                "persistence": schema.persistence.value,
                "format": schema.format,
            }
            for name, schema in contract.properties.items()
        },
    }
