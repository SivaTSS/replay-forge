#!/usr/bin/env python3
"""Verify one ReplayForge evidence manifest from the command line."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from replayforge.evidence.integrity import verify_run_manifest
from replayforge.evidence.local_store import LocalEvidenceStore
from replayforge.shared.clock import SystemClock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest_key", help="Opaque evidence:// manifest key")
    parser.add_argument("--root", type=Path, default=Path("evidence/runtime"))
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="permit an in-progress manifest without a terminal result",
    )
    arguments = parser.parse_args()
    if not arguments.root.is_dir():
        parser.error("evidence root does not exist or is not a directory")

    verification = verify_run_manifest(
        LocalEvidenceStore(arguments.root, SystemClock()),
        arguments.manifest_key,
        require_terminal=not arguments.allow_incomplete,
    )
    print(json.dumps(asdict(verification), sort_keys=True))


if __name__ == "__main__":
    main()
