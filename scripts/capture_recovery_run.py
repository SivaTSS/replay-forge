#!/usr/bin/env python3
"""Exercise bounded interstitial recovery through the public replay API."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.request import Request, urlopen


def invoke(base_url: str) -> dict[str, Any]:
    payload = json.dumps(
        {
            "tenant": "harbor",
            "version": "1.0.1",
            "inputs": {"member_id": "12345"},
        },
        separators=(",", ":"),
    ).encode()
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/capabilities/member.lookup_savings_balance/invoke",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        parsed = json.loads(response.read())
    if not isinstance(parsed, dict):
        raise RuntimeError("ReplayForge returned a non-object replay result")
    return parsed


def capture(base_url: str) -> dict[str, str]:
    result = invoke(base_url)
    if result.get("status") != "success":
        raise RuntimeError("recovery replay did not return success")
    checkpoint = result.get("checkpoint")
    if not isinstance(checkpoint, dict) or checkpoint.get("verified") is not True:
        raise RuntimeError("recovery replay did not verify its checkpoint")
    outputs = result.get("outputs")
    if not isinstance(outputs, dict) or outputs.get("available_balance") != "1420.57":
        raise RuntimeError("recovery replay did not return the expected synthetic balance")
    manifest = result.get("evidence_manifest")
    if not isinstance(manifest, str) or not manifest.startswith("evidence://run_"):
        raise RuntimeError("recovery replay omitted its evidence manifest")
    return {
        "evidence_manifest": manifest,
        "run_id": str(result["run_id"]),
        "status": str(result["status"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    print(json.dumps(capture(arguments.base_url), sort_keys=True))


if __name__ == "__main__":
    main()
