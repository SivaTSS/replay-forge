#!/usr/bin/env python3
"""Verify every stable evidence scenario under a root directory."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from replayforge.evidence.export import EvidenceExport, verify_evidence_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("evidence"))
    arguments = parser.parse_args()
    manifests = sorted(arguments.root.glob("*/manifest.json"))
    results = [verify_evidence_bundle(path.parent) for path in manifests]
    print(json.dumps([_json_result(result) for result in results], sort_keys=True))


def _json_result(result: EvidenceExport) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(result).items()}


if __name__ == "__main__":
    main()
