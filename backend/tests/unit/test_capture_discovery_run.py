"""Tests for the genuine discovery capture boundary."""

from __future__ import annotations

import os
import stat
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from replayforge.capabilities import artifact_content_hash, load_artifact_yaml
from replayforge.evidence import discovery_capture
from replayforge.evidence.discovery_capture import (
    SuiteCaptureRequest,
    _request_json,
    capture_suite,
    validate_result,
    write_new_artifact,
)


class JsonResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def discovery_result() -> dict[str, Any]:
    artifact = load_artifact_yaml(
        Path("capabilities/member.lookup_savings_balance/1.0.0.yaml").read_text()
    )
    run_id = "run_0123456789abcdef0123456789abcdef"
    manifest = f"evidence://{run_id}/manifest.json"
    provenance_manifest = f"evidence://{run_id}/manifest-compile-snapshot.bin"
    artifact = artifact.model_copy(
        update={
            "provenance": artifact.provenance.model_copy(
                update={
                    "artifact_content_hash": None,
                    "discovery_run_id": run_id,
                    "evidence_manifest_key": provenance_manifest,
                    "model": "test-vision-model",
                    "provider": "openai",
                }
            )
        }
    )
    artifact = artifact.model_copy(
        update={
            "provenance": artifact.provenance.model_copy(
                update={"artifact_content_hash": artifact_content_hash(artifact)}
            )
        }
    )
    return {
        "artifact": artifact.model_dump(mode="json"),
        "evidence_manifest": manifest,
        "run_id": run_id,
        "status": "success",
    }


def test_validates_and_writes_owner_only_artifact(tmp_path: Path) -> None:
    artifact = validate_result(discovery_result())
    output = tmp_path / "discovered.yaml"

    write_new_artifact(output, artifact)

    assert load_artifact_yaml(output.read_text()) == artifact
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_invokes_caller_supplied_discovery_without_task_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: int) -> JsonResponse:
        observed.update(url=request.full_url, timeout=timeout, body=request.data)
        return JsonResponse(b'{"status":"success"}')

    monkeypatch.setattr(discovery_capture, "urlopen", fake_urlopen)

    payload = {"goal": "Find a shipment", "inputs": {"tracking": "SHIP-42"}}
    assert _request_json(
        "http://localhost:8000/", "/api/v1/discoveries", 120, method="POST", payload=payload
    ) == {"status": "success"}
    assert observed["url"] == "http://localhost:8000/api/v1/discoveries"
    assert observed["timeout"] == 150
    body = observed["body"]
    assert isinstance(body, bytes)
    assert b'"tracking":"SHIP-42"' in body
    assert b"member" not in body


def test_rejects_http_and_non_object_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> JsonResponse:
        raise HTTPError("http://localhost", 503, "Unavailable", Message(), None)

    monkeypatch.setattr(discovery_capture, "urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        _request_json("http://localhost:8000", "/api/v1/discovery-suites", 120)

    monkeypatch.setattr(
        discovery_capture,
        "urlopen",
        lambda *_args, **_kwargs: JsonResponse(b"[]"),
    )
    with pytest.raises(RuntimeError, match="non-object"):
        _request_json("http://localhost:8000", "/api/v1/discovery-suites", 120)


def test_suite_capture_requires_real_provenance_and_validated_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = discovery_result()
    artifact = result["artifact"]
    request = SuiteCaptureRequest(
        goal="Look up the synthetic member balance",
        application_family="northstar_member_service",
        tenant="harbor",
        entry_point="visual_member_workbench",
        inputs={"member_id": "12345"},
        validation_tenants=("summit",),
        expected_capability_id="member.lookup_savings_balance",
        expected_risk="read_only",
        expected_inputs=("member_id",),
        expected_outputs=("member_id", "account_type", "currency", "available_balance", "as_of"),
    )
    monkeypatch.setattr(
        discovery_capture,
        "invoke_suite",
        lambda *_args: {
            "suite": {"suite_id": "run_suite", "status": "published"},
            "primary": {
                "status": "success",
                "run_id": result["run_id"],
                "evidence_manifest": result["evidence_manifest"],
            },
            "artifact": artifact,
        },
    )
    output = tmp_path / "suite.yaml"

    summary = capture_suite("http://localhost:8000", 120, output, request)

    assert summary["suite_id"] == "run_suite"
    assert summary["capability_id"] == "member.lookup_savings_balance"
    assert output.is_file()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"status": "failure"}, "did not return success"),
        ({"evidence_manifest": "evidence://another/manifest.json"}, "run-bound"),
    ],
)
def test_rejects_invalid_discovery_envelope(mutation: dict[str, Any], message: str) -> None:
    result = discovery_result() | mutation

    with pytest.raises(RuntimeError, match=message):
        validate_result(result)


def test_rejects_non_openai_artifact() -> None:
    result = discovery_result()
    result["artifact"]["provenance"]["provider"] = "scripted"

    with pytest.raises(RuntimeError, match="OpenAI provider"):
        validate_result(result)


def test_rejects_cross_run_artifact_provenance() -> None:
    result = discovery_result()
    result["artifact"]["provenance"]["evidence_manifest_key"] = (
        "evidence://run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/manifest.json"
    )

    with pytest.raises(RuntimeError, match="different run"):
        validate_result(result)


def test_rejects_artifact_hash_mismatch() -> None:
    result = discovery_result()
    result["artifact"]["capability"]["description"] = "Tampered after compilation"

    with pytest.raises(RuntimeError, match="content-hash"):
        validate_result(result)


def test_refuses_to_overwrite_artifact(tmp_path: Path) -> None:
    output = tmp_path / "existing.yaml"
    output.write_text("reviewed: true\n")

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        write_new_artifact(output, validate_result(discovery_result()))

    assert output.read_text() == "reviewed: true\n"


def test_rejects_missing_output_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="directory does not exist"):
        write_new_artifact(
            tmp_path / "missing" / "artifact.yaml",
            validate_result(discovery_result()),
        )


def test_removes_partial_artifact_when_durable_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "partial.yaml"
    monkeypatch.setattr(
        os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("disk failure")),
    )

    with pytest.raises(OSError, match="disk failure"):
        write_new_artifact(output, validate_result(discovery_result()))

    assert not output.exists()
