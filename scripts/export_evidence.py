#!/usr/bin/env python3
"""Export a verified runtime manifest into a stable evidence scenario directory."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from replayforge.capabilities.serialization import load_artifact_yaml
from replayforge.evidence.export import EvidenceExportRequest, export_evidence_bundle
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.shared.clock import SystemClock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_manifest_key")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--root", type=Path, default=Path("evidence/runtime"))
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--command", action="append", required=True)
    arguments = parser.parse_args()
    if not arguments.root.is_dir():
        parser.error("evidence root does not exist or is not a directory")
    if not arguments.artifact.is_file() or arguments.artifact.stat().st_size > 1_000_000:
        parser.error("artifact must be an existing YAML file no larger than one megabyte")

    result = export_evidence_bundle(
        LocalEvidenceStore(arguments.root, SystemClock()),
        arguments.destination,
        EvidenceExportRequest(
            scenario=arguments.scenario,
            artifact=load_artifact_yaml(arguments.artifact.read_text(encoding="utf-8")),
            source_manifest_key=arguments.source_manifest_key,
            commands=tuple(arguments.command),
            commit_sha=arguments.commit_sha,
        ),
    )
    print(json.dumps({key: str(value) for key, value in asdict(result).items()}, sort_keys=True))


if __name__ == "__main__":
    main()
