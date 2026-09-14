#!/usr/bin/env python3
"""Capture the genuine provider-backed demo workflow discovery suites."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from replayforge.api.contracts import DiscoverySuiteScenario
from replayforge.evidence.discovery_capture import (
    ScenarioCaptureRequest,
    SuiteCaptureRequest,
    capture_suite,
)
from replayforge.shared.yaml import load_unique_yaml


def _workflow_request(raw: dict[str, Any]) -> SuiteCaptureRequest:
    scenarios = tuple(
        DiscoverySuiteScenario.model_validate(item) for item in raw.get("scenarios", [])
    )
    return SuiteCaptureRequest(
        goal=str(raw["goal"]),
        application_family=str(raw["application_family"]),
        tenant=str(raw["tenant"]),
        entry_point=str(raw["entry_point"]),
        inputs=dict(raw["inputs"]),
        validation_tenants=tuple(raw["validation_tenants"]),
        expected_capability_id=str(raw["expected_capability_id"]),
        expected_risk=str(raw["expected_risk"]),
        expected_inputs=tuple(raw["expected_inputs"]),
        expected_outputs=tuple(raw["expected_outputs"]),
        scenarios=tuple(
            ScenarioCaptureRequest(
                code=item.code or f"scenario_{index}",
                kind=item.kind,
                goal=item.goal,
                inputs=item.inputs,
            )
            for index, item in enumerate(scenarios, start=1)
        ),
        primary_version=raw.get("primary_version"),
        publish=not raw.get("collect_only", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--spec", type=Path, required=True, help="Goal-only discovery suite specification"
    )
    parser.add_argument("--workflow", action="append", help="Workflow key; omit to capture all")
    parser.add_argument("--scenario", action="append", help="Collect only these scenario codes")
    parser.add_argument("--resume-suite", help="Resume validation for one existing suite ID")
    parser.add_argument(
        "--primary-version", help="Extend this published version after fresh replay"
    )
    parser.add_argument("--output-directory", type=Path, default=Path(".local/discovery-captures"))
    parser.add_argument(
        "--collect-only", action="store_true", help="Retain scenario evidence without publication"
    )
    arguments = parser.parse_args()
    if not 10 <= arguments.timeout_seconds <= 600:
        parser.error("timeout must be between 10 and 600 seconds")
    raw = load_unique_yaml(arguments.spec.read_text(encoding="utf-8"))
    workflows = raw.get("workflows") if isinstance(raw, dict) else None
    if not isinstance(workflows, dict) or not workflows:
        parser.error("spec must contain a non-empty workflows mapping")
    if any(
        not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name)
        for name in workflows
    ):
        parser.error("workflow names must be safe lowercase identifiers")
    selected = arguments.workflow or list(workflows)
    unknown = sorted(set(selected) - set(workflows))
    if unknown:
        parser.error(f"unknown workflow: {', '.join(unknown)}")
    if arguments.resume_suite and len(selected) != 1:
        parser.error("--resume-suite requires exactly one --workflow")
    if arguments.scenario:
        if len(selected) != 1 or not arguments.collect_only or not arguments.primary_version:
            parser.error("--scenario requires one workflow, --collect-only and --primary-version")
        known_codes = {item.get("code") for item in workflows[selected[0]].get("scenarios", [])}
        if not set(arguments.scenario) <= known_codes:
            parser.error("unknown scenario code")
    if arguments.primary_version and (
        len(selected) != 1
        or arguments.resume_suite
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", arguments.primary_version)
    ):
        parser.error("--primary-version requires one workflow, a semantic version, and no resume")

    arguments.output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    capture_directory = Path(tempfile.mkdtemp(prefix="capture-", dir=arguments.output_directory))
    summaries: dict[str, dict[str, str]] = {}
    for name in selected:
        item = workflows[name]
        if not isinstance(item, dict):
            parser.error(f"workflow {name} must be a mapping")
        output = capture_directory / f"{name}.yaml"
        summaries[name] = capture_suite(
            arguments.base_url,
            arguments.timeout_seconds,
            output,
            _workflow_request(
                {
                    **item,
                    "scenarios": [
                        scenario
                        for scenario in item.get("scenarios", [])
                        if not arguments.scenario or scenario.get("code") in arguments.scenario
                    ],
                    "collect_only": arguments.collect_only,
                    **(
                        {"primary_version": arguments.primary_version}
                        if arguments.primary_version
                        else {}
                    ),
                }
            ),
            arguments.resume_suite,
        )
    print(json.dumps(summaries, sort_keys=True))


if __name__ == "__main__":
    main()
