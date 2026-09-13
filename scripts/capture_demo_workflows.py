#!/usr/bin/env python3
"""Capture the genuine provider-backed demo workflow discovery suites."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from replayforge.evidence.discovery_capture import SuiteCaptureRequest, capture_suite


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
    parser.add_argument("--spec", type=Path, default=Path("config/demo-discovery.yaml"))
    parser.add_argument("--workflow", action="append", help="Workflow key; omit to capture all")
    arguments = parser.parse_args()
    if not 10 <= arguments.timeout_seconds <= 600:
        parser.error("timeout must be between 10 and 600 seconds")
    raw = yaml.safe_load(arguments.spec.read_text(encoding="utf-8"))
    workflows = raw.get("workflows") if isinstance(raw, dict) else None
    if not isinstance(workflows, dict) or not workflows:
        parser.error("spec must contain a non-empty workflows mapping")
    selected = arguments.workflow or list(workflows)
    unknown = sorted(set(selected) - set(workflows))
    if unknown:
        parser.error(f"unknown workflow: {', '.join(unknown)}")

    summaries: dict[str, dict[str, str]] = {}
    for name in selected:
        item = workflows[name]
        if not isinstance(item, dict):
            parser.error(f"workflow {name} must be a mapping")
        output = Path(str(item["artifact_output"]))
        output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        summaries[name] = capture_suite(
            arguments.base_url,
            arguments.timeout_seconds,
            output,
            _workflow_request(item),
        )
    print(json.dumps(summaries, sort_keys=True))


if __name__ == "__main__":
    main()
