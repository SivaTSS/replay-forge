#!/usr/bin/env python3
"""Capture the genuine provider-backed demo workflow discovery suites."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from replayforge.evidence.discovery_capture import SuiteCaptureRequest, capture_suite
from replayforge.shared.yaml import load_unique_yaml


def _workflow_request(raw: dict[str, Any]) -> SuiteCaptureRequest:
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
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--spec", type=Path, required=True, help="Goal-only discovery suite specification"
    )
    parser.add_argument("--workflow", action="append", help="Workflow key; omit to capture all")
    parser.add_argument("--resume-suite", help="Resume validation for one existing suite ID")
    parser.add_argument("--output-directory", type=Path, default=Path(".local/discovery-captures"))
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
            _workflow_request(item),
            arguments.resume_suite,
        )
    print(json.dumps(summaries, sort_keys=True))


if __name__ == "__main__":
    main()
