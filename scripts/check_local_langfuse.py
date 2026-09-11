#!/usr/bin/env python3
"""Verify the configured local Langfuse endpoint and project credentials."""

from __future__ import annotations

import json

from replayforge.observability.model_calls import LangfuseModelCallTelemetry
from replayforge.runtime.settings import RuntimeSettings


def main() -> None:
    settings = RuntimeSettings()
    if settings.langfuse_public_key is None or settings.langfuse_secret_key is None:
        raise SystemExit("local Langfuse credentials are not configured")
    telemetry = LangfuseModelCallTelemetry.create(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_base_url,
        policy=settings.model_policy,
    )
    try:
        if not telemetry.ready():
            raise SystemExit("local Langfuse authentication failed")
        print(json.dumps({"authenticated": True, "base_url": settings.langfuse_base_url}))
    finally:
        telemetry.close()


if __name__ == "__main__":
    main()
