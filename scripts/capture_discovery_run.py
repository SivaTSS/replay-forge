#!/usr/bin/env python3
"""Capture and validate a genuine OpenAI-driven discovery through the public API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from replayforge.evidence.discovery_capture import capture


def timeout_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be an integer") from exc
    if not 10 <= parsed <= 600:
        raise argparse.ArgumentTypeError("timeout must be between 10 and 600 seconds")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=timeout_seconds, default=120, metavar="SECONDS")
    parser.add_argument("--artifact-output", required=True, type=Path)
    arguments = parser.parse_args()
    print(
        json.dumps(
            capture(arguments.base_url, arguments.timeout_seconds, arguments.artifact_output),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
