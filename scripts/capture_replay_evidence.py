#!/usr/bin/env python3
"""Run declared model-free replay cases and export verified, sanitized evidence."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from replayforge.api.contracts import ReplayInvocation
from replayforge.evidence.export import EvidenceExportRequest, export_evidence_bundle
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.runs.results import BusinessOutcomeResult, FailureResult, RunResult, SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.shared.yaml import load_unique_yaml

ScenarioName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")]


class ReplayCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    invocation: ReplayInvocation
    expected_status: Literal["success", "failure", "business_outcome"]
    expected_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    expected_outputs: dict[str, JsonValue] | None = None
    expected_recovery: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def validate_proof_contract(self) -> ReplayCase:
        if self.expected_status != "success":
            if self.expected_code is None:
                raise ValueError("negative replay requires an exact expected code")
            if self.expected_outputs is not None or self.expected_recovery is not None:
                raise ValueError("outputs and completed recovery require successful replay")
        elif self.expected_code is not None:
            raise ValueError("successful replay has no negative outcome code")
        return self


class ReplaySpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: dict[ScenarioName, ReplayCase] = Field(min_length=1, max_length=20)


def validate_result(
    case: ReplayCase, result: RunResult, completed_recoveries: tuple[str, ...] = ()
) -> None:
    # Report only a stable mismatch, never supplied inputs or returned financial values.
    if result.status != case.expected_status:
        raise ValueError("replay status did not match the declared expectation")
    if case.expected_code is not None and (
        not isinstance(result, FailureResult | BusinessOutcomeResult)
        or result.code != case.expected_code
    ):
        raise ValueError("replay outcome/failure code did not match the declared expectation")
    if case.expected_outputs is not None and (
        not isinstance(result, SuccessResult) or result.outputs != case.expected_outputs
    ):
        raise ValueError("replay outputs did not match the declared expectation")
    if case.expected_recovery is not None and (
        not isinstance(result, SuccessResult) or case.expected_recovery not in completed_recoveries
    ):
        raise ValueError("replay did not complete the declared recovery")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("config/replay-evidence.yaml"))
    parser.add_argument("--case", action="append", help="Case name; omit to run all")
    parser.add_argument(
        "--commit-sha", required=True, help="Actual source revision used for capture"
    )
    parser.add_argument("--output-root", type=Path, default=Path(".local/replay-captures"))
    arguments = parser.parse_args()
    try:
        specification = ReplaySpecification.model_validate(
            load_unique_yaml(arguments.spec.read_text(encoding="utf-8"))
        )
    except ValueError:
        parser.error("invalid replay specification")
    selected = arguments.case or list(specification.cases)
    if len(set(selected)) != len(selected) or set(selected) - specification.cases.keys():
        parser.error("case names must be unique and present in the specification")
    # Resolve every destination before acting; never rerun a mutation just to discover an overwrite.
    if any((arguments.output_root / name).exists() for name in selected):
        parser.error("an evidence destination already exists; use a fresh output root")
    settings = RuntimeSettings(
        openai_api_key=None, langfuse_public_key=None, langfuse_secret_key=None
    )
    runtime = build_runtime(settings)
    try:
        for name in selected:
            case = specification.cases[name]
            record = (
                runtime.service.registry.get(case.capability_id, case.invocation.version)
                if case.invocation.version
                else runtime.service.registry.latest(case.capability_id)
            )
            command = shlex.join(
                [
                    "uv",
                    "run",
                    "python",
                    "scripts/capture_replay_evidence.py",
                    "--spec",
                    str(arguments.spec),
                    "--case",
                    name,
                    "--commit-sha",
                    arguments.commit_sha,
                    "--output-root",
                    str(arguments.output_root),
                ]
            )
            # Validate provenance metadata before starting the browser.
            request = EvidenceExportRequest(
                scenario=name,
                artifact=record.artifact,
                source_manifest_key="pending",
                commands=(command,),
                commit_sha=arguments.commit_sha,
            )
            result = runtime.service.invoke(
                case.capability_id,
                record.artifact.capability.version,
                case.invocation.tenant,
                case.invocation.inputs,
            )
            events = runtime.journals[result.run_id].events()
            completed = tuple(
                str(event.details["recovery_id"])
                for event in events
                if event.event_type == "recovery_completed" and "recovery_id" in event.details
            )
            validate_result(case, result, completed)
            if not isinstance(result, SuccessResult | FailureResult | BusinessOutcomeResult):
                raise ValueError("replay did not produce a completed result")
            if any(event.event_type.startswith("model_") for event in events):
                raise ValueError("model activity is not permitted in replay evidence")
            export = export_evidence_bundle(
                LocalEvidenceStore(settings.evidence_directory, SystemClock()),
                arguments.output_root / name,
                EvidenceExportRequest(
                    scenario=request.scenario,
                    artifact=request.artifact,
                    source_manifest_key=result.evidence_manifest,
                    commands=request.commands,
                    commit_sha=request.commit_sha,
                ),
            )
            print(json.dumps({"scenario": name, "run_id": export.run_id, "status": result.status}))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
