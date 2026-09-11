#!/usr/bin/env python3
"""Provision matching local Langfuse server and ReplayForge client credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from replayforge.runtime.langfuse_bootstrap import provision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-env", type=Path, default=Path(".env"))
    parser.add_argument("--server-env", type=Path, default=Path(".secrets/langfuse-server.env"))
    arguments = parser.parse_args()
    created = provision(arguments.application_env, arguments.server_env)
    print(
        json.dumps(
            {
                "application_env": str(arguments.application_env),
                "credentials_created": created,
                "server_env": str(arguments.server_env),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
