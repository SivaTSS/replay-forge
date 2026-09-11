#!/usr/bin/env python3
"""Exercise a declared permission failure through the public replay API."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.request import Request, urlopen


def invoke(base_url: str) -> dict[str, Any]:
    payload = json.dumps(
        {
            "tenant": "harbor",
            "version": "1.0.2",
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
    if result.get("status") != "failure" or result.get("code") != "permission_denied":
        raise RuntimeError("permission scenario did not return the declared typed failure")
    if result.get("step_id") != "search.submit":
        raise RuntimeError("permission failure was not attributed to search submission")
    if result.get("expected") != {"state": "member_results"}:
        raise RuntimeError("permission failure omitted its expected state")
    if result.get("observed") != {"state": "permission_denied"}:
        raise RuntimeError("permission failure omitted its observed state")
    manifest = result.get("evidence_manifest")
    if not isinstance(manifest, str) or not manifest.startswith("evidence://run_"):
        raise RuntimeError("permission failure omitted its evidence manifest")
    return {
        "code": str(result["code"]),
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
