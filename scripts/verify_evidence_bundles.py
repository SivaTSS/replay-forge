#!/usr/bin/env python3
"""Verify every stable evidence scenario under a root directory."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from replayforge.evidence.export import EvidenceExport, verify_evidence_bundle


def submission_gaps(bundles: list[Path]) -> set[str]:
    """Check coverage only after each bundle has passed integrity verification."""
    found: set[str] = set()
    for bundle in bundles:
        events = {
            json.loads(line)["event_type"]
            for line in (bundle / "events.jsonl").read_text().splitlines()
        }
        result = json.loads((bundle / "result.json").read_text())
        if {
            "discovery_started",
            "model_proposal_received",
            "artifact_compiled",
        } <= events and result["status"] == "success":
            found.add("genuine discovery")
        if "replay_started" in events and not any(name.startswith("model_") for name in events):
            if result["status"] == "success":
                found.add("successful model-free replay")
            if result["status"] == "failure":
                manifest = json.loads((bundle / "manifest.json").read_text())
                if manifest["attachments"]:
                    found.add("failed replay with richer evidence")
    return {
        "genuine discovery",
        "successful model-free replay",
        "failed replay with richer evidence",
    } - found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("evidence"))
    parser.add_argument("--require-submission", action="store_true")
    arguments = parser.parse_args()
    manifests = sorted(arguments.root.glob("*/manifest.json"))
    if not manifests:
        parser.error("no evidence bundles found; an empty directory is not verification")
    results = [verify_evidence_bundle(path.parent) for path in manifests]
    if arguments.require_submission:
        missing = submission_gaps([path.parent for path in manifests])
        if missing:
            parser.error("submission evidence missing: " + ", ".join(sorted(missing)))
    print(json.dumps([_json_result(result) for result in results], sort_keys=True))


def _json_result(result: EvidenceExport) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(result).items()}


if __name__ == "__main__":
    main()
