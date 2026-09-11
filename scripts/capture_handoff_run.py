#!/usr/bin/env python3
"""Exercise the approval-gated replay handoff through the public HTTP API."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class HttpResponse:
    body: bytes
    headers: dict[str, str]

    def json(self) -> dict[str, Any]:
        parsed = json.loads(self.body)
        if not isinstance(parsed, dict):
            raise RuntimeError("ReplayForge returned a non-object JSON response")
        return parsed


def request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> HttpResponse:
    content = None
    headers: dict[str, str] = {}
    if payload is not None:
        content = json.dumps(payload, separators=(",", ":")).encode()
        headers["content-type"] = "application/json"
    message = Request(
        f"{base_url.rstrip('/')}{path}",
        data=content,
        headers=headers,
        method=method,
    )
    with urlopen(message, timeout=30) as response:
        return HttpResponse(
            response.read(),
            {name.lower(): value for name, value in response.headers.items()},
        )


def capture(base_url: str, operator_id: str) -> dict[str, str]:
    paused = request(
        base_url,
        "POST",
        "/api/v1/capabilities/member.lookup_savings_balance/invoke",
        {
            "tenant": "harbor",
            "version": "2.0.0",
            "inputs": {"member_id": "12345"},
        },
    ).json()
    if paused.get("status") != "intervention_required":
        raise RuntimeError("approval-gated replay did not create an intervention")
    intervention_id = str(paused["intervention_id"])
    transition = request(base_url, "GET", f"/api/v1/interventions/{intervention_id}").json()
    claimed = request(
        base_url,
        "POST",
        f"/api/v1/interventions/{intervention_id}/claim",
        {
            "expected_lease_version": int(transition["lease_version"]),
            "operator_id": operator_id,
        },
    ).json()
    lease_version = int(claimed["lease_version"])
    frame = request(
        base_url,
        "GET",
        f"/api/v1/interventions/{intervention_id}/viewport"
        f"?expected_lease_version={lease_version}&operator_id={operator_id}",
    )
    if not frame.body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("intervention viewport did not return PNG content")
    required_headers = (
        "x-replayforge-frame-sequence",
        "x-replayforge-viewport-width",
        "x-replayforge-viewport-height",
        "x-replayforge-next-client-sequence",
    )
    if any(name not in frame.headers for name in required_headers):
        raise RuntimeError("intervention viewport omitted sequence metadata")
    request(
        base_url,
        "POST",
        f"/api/v1/interventions/{intervention_id}/input",
        {
            "expected_lease_version": lease_version,
            "operator_id": operator_id,
            "client_sequence": int(frame.headers["x-replayforge-next-client-sequence"]),
            "source_frame_sequence": int(frame.headers["x-replayforge-frame-sequence"]),
            "viewport_width": int(frame.headers["x-replayforge-viewport-width"]),
            "viewport_height": int(frame.headers["x-replayforge-viewport-height"]),
            "input": {"kind": "key", "key": "Enter"},
        },
    ).json()
    resumed = request(
        base_url,
        "POST",
        f"/api/v1/interventions/{intervention_id}/resume",
        {
            "expected_lease_version": lease_version,
            "operator_id": operator_id,
        },
    ).json()
    result = resumed.get("result")
    if resumed.get("status") != "resolved" or not isinstance(result, dict):
        raise RuntimeError("handoff did not resolve with a typed replay result")
    if result.get("status") != "success" or not result.get("checkpoint", {}).get("verified"):
        raise RuntimeError("resumed replay did not satisfy its success checkpoint")
    return {
        "evidence_manifest": str(result["evidence_manifest"]),
        "intervention_id": intervention_id,
        "run_id": str(result["run_id"]),
        "status": str(result["status"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--operator-id", default="reviewer-1")
    arguments = parser.parse_args()
    print(json.dumps(capture(arguments.base_url, arguments.operator_id), sort_keys=True))


if __name__ == "__main__":
    main()
