#!/usr/bin/env python3
"""Revalidate existing genuine scenario bundles after restart, with no model configured."""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from capture_demo_workflows import _workflow_request

from replayforge.capabilities.models import CapabilityArtifact
from replayforge.evidence.export import EvidenceExportRequest, export_evidence_bundle
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.evidence.scenario_restore import restore_scenario
from replayforge.runs.discovery_suite import ReplayValidation
from replayforge.runs.results import BusinessOutcomeResult, FailureResult, SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from replayforge.shared.yaml import load_unique_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--primary-version", required=True)
    parser.add_argument("--scenario", required=True, action="append", help="code=bundle_directory")
    parser.add_argument("--output-root", type=Path, help="Export fresh validation replay bundles")
    parser.add_argument("--evidence-prefix", default="scenario")
    parser.add_argument("--commit-sha", help="Actual source revision used for these replays")
    args = parser.parse_args()
    raw = load_unique_yaml(args.spec.read_text())
    request = _workflow_request(raw["workflows"][args.workflow])
    paths: dict[str, Path] = {}
    for item in args.scenario:
        code, separator, path = item.partition("=")
        if not separator or code in paths:
            parser.error("scenario mappings must be unique code=directory pairs")
        paths[code] = Path(path)
    if set(paths) != {scenario.code for scenario in request.scenarios}:
        parser.error("provide exactly the scenarios declared by the workflow")
    # Verify all files before running any browser action.
    scenarios = tuple(restore_scenario(paths[item.code], item) for item in request.scenarios)
    if args.output_root and (args.output_root.exists() or not args.commit_sha):
        parser.error("export requires a new output root and actual --commit-sha")
    settings = RuntimeSettings(
        openai_api_key=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    if args.output_root:
        if re.fullmatch(r"[0-9a-f]{7,40}", args.commit_sha) is None:
            parser.error("invalid source commit")
        for tenant in (request.tenant, *request.validation_tenants):
            for code in ("primary", *(item.code for item in request.scenarios)):
                name = f"replay-{args.evidence_prefix}-{tenant}-{code.replace('_', '-')}"
                if re.fullmatch(r"[a-z][a-z0-9-]{1,63}", name) is None:
                    parser.error("export name is invalid; use a shorter safe evidence prefix")
    runtime = build_runtime(settings)
    try:
        service = runtime.discovery_suite_service
        validator = service.validator
        assert validator is not None
        proofs: dict[tuple[str, str], tuple[CapabilityArtifact, ReplayValidation]] = {}

        def validate(
            artifact: CapabilityArtifact, tenant: str, inputs: dict[str, Any]
        ) -> ReplayValidation:
            proof = validator(artifact, tenant, inputs)
            if artifact.outcomes or artifact.failures or artifact.recoveries:
                code = next(
                    (item.code for item in request.scenarios if item.inputs == inputs), "primary"
                )
                proofs[(tenant, code)] = (artifact, proof)
            return proof

        service.validator = validate
        suite = service.from_published(
            capability_id=request.expected_capability_id,
            version=args.primary_version,
            tenant=request.tenant,
            inputs=request.inputs,
        )
        service.restore_scenarios(suite.suite_id, scenarios)
        for tenant in request.validation_tenants:
            service.validate(suite.suite_id, tenant=tenant, inputs=request.inputs)
        published = service.finalize(suite.suite_id)
        if args.output_root:
            store = LocalEvidenceStore(settings.evidence_directory, SystemClock())
            for (tenant, code), (artifact, proof) in proofs.items():
                if not isinstance(
                    proof.result, SuccessResult | FailureResult | BusinessOutcomeResult
                ):
                    raise ValueError(
                        "paused execution cannot be exported as completed replay proof"
                    )
                name = f"replay-{args.evidence_prefix}-{tenant}-{code.replace('_', '-')}"
                events = runtime.journals[proof.result.run_id].events()
                if any(event.event_type.startswith("model_") for event in events):
                    raise ValueError("model events cannot appear in restored replay proof")
                export_evidence_bundle(
                    store,
                    args.output_root / name,
                    EvidenceExportRequest(
                        scenario=name,
                        artifact=artifact,
                        source_manifest_key=proof.result.evidence_manifest,
                        commands=(shlex.join(["uv", "run", "python", *sys.argv]),),
                        commit_sha=args.commit_sha,
                    ),
                )
        print(json.dumps(published.snapshot(), sort_keys=True))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
