"""Secure capture boundary for a genuine model-driven discovery run."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from replayforge.capabilities import (
    CapabilityArtifact,
    artifact_content_hash,
    dump_artifact_yaml,
)
from replayforge.runs.discovery_suite import ScenarioKind
from replayforge.runs.results import ArtifactPrivacyDiagnostic


@dataclass(frozen=True, slots=True)
class ScenarioCaptureRequest:
    code: str
    kind: ScenarioKind
    goal: str
    inputs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SuiteCaptureRequest:
    goal: str
    application_family: str
    tenant: str
    entry_point: str
    inputs: dict[str, Any]
    validation_tenants: tuple[str, ...]
    expected_capability_id: str
    expected_risk: str
    expected_inputs: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    max_steps: int = 40
    scenarios: tuple[ScenarioCaptureRequest, ...] = ()
    primary_version: str | None = None
    publish: bool = True


def _request_json(
    base_url: str,
    path: str,
    timeout_seconds: int,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    encoded = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=encoded,
        headers={"content-type": "application/json"} if encoded is not None else {},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds + 30) as response:
            parsed = json.loads(response.read())
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read())
        except (json.JSONDecodeError, OSError):
            error_payload = None
        error_code = error_payload.get("code") if isinstance(error_payload, dict) else None
        suffix = f" ({error_code})" if isinstance(error_code, str) else ""
        raise RuntimeError(f"discovery API returned HTTP {exc.code}{suffix} for {path}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("ReplayForge returned a non-object discovery result")
    return parsed


def invoke_suite(
    base_url: str,
    timeout_seconds: int,
    request: SuiteCaptureRequest,
    suite_id: str | None = None,
    on_scenario: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    suite = (
        _request_json(
            base_url,
            f"/api/v1/discovery-suites/{suite_id}",
            timeout_seconds,
        )
        if suite_id is not None
        else _request_json(
            base_url,
            (
                "/api/v1/discovery-suites/from-published"
                if request.primary_version
                else "/api/v1/discovery-suites"
            ),
            timeout_seconds,
            method="POST",
            payload={
                "capability_id": request.expected_capability_id,
                "version": request.primary_version,
                "tenant": request.tenant,
                "inputs": request.inputs,
            }
            if request.primary_version
            else {
                "goal": request.goal,
                "application_family": request.application_family,
                "tenant": request.tenant,
                "entry_point": request.entry_point,
                "inputs": request.inputs,
                "existing_capability_id": request.expected_capability_id,
                "max_steps": request.max_steps,
                "timeout_seconds": timeout_seconds,
            },
        )
    )
    suite_id = suite.get("suite_id")
    primary = suite.get("primary")
    if not isinstance(suite_id, str) or not isinstance(primary, dict):
        raise RuntimeError("discovery suite response is incomplete")
    if primary.get("status") != "success":
        code = primary.get("code")
        suffix = f" ({code})" if isinstance(code, str) else ""
        diagnostic = primary.get("privacy_rejection")
        if diagnostic is not None:
            safe = ArtifactPrivacyDiagnostic.model_validate(diagnostic)
            suffix += f": literal {safe.source} value at {safe.location}"
        raise RuntimeError(
            f"model-driven discovery suite {suite_id} did not return success{suffix}"
        )

    metadata = suite.get("artifact")
    if not isinstance(metadata, dict):
        raise RuntimeError("discovery suite omitted its draft contract")
    contract = metadata.get("input_contract")
    if (
        metadata.get("capability_id") != request.expected_capability_id
        or metadata.get("risk") != request.expected_risk
        or not isinstance(contract, dict)
        or set(contract.get("required", [])) != set(request.expected_inputs)
        or set(metadata.get("output_fields", [])) != set(request.expected_outputs)
    ):
        raise RuntimeError(
            "discovered draft does not match the configured capture contract; not published"
        )

    scenario_results: dict[str, Any] = {}
    for scenario in request.scenarios:
        existing = next(
            (item for item in suite.get("scenarios", []) if item.get("code") == scenario.code), None
        )
        if existing is None:
            suite = _request_json(
                base_url,
                f"/api/v1/discovery-suites/{suite_id}/scenarios",
                timeout_seconds,
                method="POST",
                payload={
                    "kind": scenario.kind,
                    "code": scenario.code,
                    "goal": scenario.goal,
                    "description": scenario.goal,
                    "inputs": scenario.inputs,
                    "max_steps": request.max_steps,
                    "timeout_seconds": timeout_seconds,
                },
            )
            existing = next(
                (item for item in suite.get("scenarios", []) if item.get("code") == scenario.code),
                None,
            )
        if not isinstance(existing, dict) or existing.get("result", {}).get("status") != "success":
            code = (
                existing.get("result", {}).get("code", "missing_result")
                if existing
                else "missing_result"
            )
            raise RuntimeError(
                f"scenario {scenario.code} in suite {suite_id} did not verify ({code})"
            )
        scenario_results[scenario.code] = {
            **existing["result"],
            "artifact": _request_json(
                base_url,
                f"/api/v1/discovery-suites/{suite_id}/scenarios/{scenario.code}/artifact",
                timeout_seconds,
            ),
        }
        if on_scenario is not None:
            on_scenario(scenario.code, scenario_results[scenario.code])

    if not request.publish:
        return {"suite": suite, "primary": primary, "scenarios": scenario_results}

    for tenant in request.validation_tenants:
        suite = _request_json(
            base_url,
            f"/api/v1/discovery-suites/{suite_id}/validations",
            timeout_seconds,
            method="POST",
            payload={"tenant": tenant, "inputs": request.inputs},
        )
    suite = _request_json(
        base_url,
        f"/api/v1/discovery-suites/{suite_id}/finalize",
        timeout_seconds,
        method="POST",
        payload={},
    )
    if suite.get("status") != "published":
        raise RuntimeError("validated discovery suite was not published")
    artifact = _request_json(
        base_url,
        f"/api/v1/discovery-suites/{suite_id}/artifact",
        timeout_seconds,
    )
    return {"suite": suite, "primary": primary, "artifact": artifact, "scenarios": scenario_results}


def validate_result(result: dict[str, Any]) -> CapabilityArtifact:
    if result.get("status") != "success":
        raise RuntimeError("model-driven discovery did not return success")
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("run_"):
        raise RuntimeError("discovery result omitted a valid run ID")
    manifest = result.get("evidence_manifest")
    if not isinstance(manifest, str) or not manifest.startswith(f"evidence://{run_id}/"):
        raise RuntimeError("discovery result omitted a run-bound evidence manifest")

    artifact_payload = result.get("artifact")
    if not isinstance(artifact_payload, dict):
        raise RuntimeError("successful discovery omitted its compiled artifact")
    try:
        artifact = CapabilityArtifact.model_validate(artifact_payload)
    except ValidationError:
        raise RuntimeError("compiled artifact failed schema validation") from None
    provenance = artifact.provenance
    if provenance.discovery_run_id != run_id:
        raise RuntimeError("artifact provenance does not identify the discovery run")
    if provenance.provider != "openai":
        raise RuntimeError("artifact provenance does not identify the OpenAI provider")
    if not provenance.model.strip() or provenance.model == "not-applicable":
        raise RuntimeError("artifact provenance omitted the OpenAI model ID")
    if not provenance.evidence_manifest_key.startswith(f"evidence://{run_id}/"):
        raise RuntimeError("artifact provenance evidence belongs to a different run")

    calculated_hash = artifact_content_hash(artifact)
    if provenance.artifact_content_hash != calculated_hash:
        raise RuntimeError("compiled artifact failed canonical content-hash verification")
    return artifact


def write_new_artifact(path: Path, artifact: CapabilityArtifact) -> None:
    _write_new_private_file(path, dump_artifact_yaml(artifact).encode())


def _write_new_private_file(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise RuntimeError(f"artifact output directory does not exist: {path.parent}")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite existing artifact: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def capture_suite(
    base_url: str,
    timeout_seconds: int,
    artifact_output: Path,
    request: SuiteCaptureRequest,
    suite_id: str | None = None,
) -> dict[str, str]:
    def retain_scenario(code: str, result: dict[str, Any]) -> None:
        artifact = validate_result(result)
        path = artifact_output.with_name(f"{artifact_output.stem}.{code}.yaml")
        write_new_artifact(path, artifact)
        _write_new_private_file(
            path.with_suffix(".proof.json"),
            json.dumps(
                {
                    "status": "success",
                    "run_id": result["run_id"],
                    "evidence_manifest": result["evidence_manifest"],
                    "artifact_content_hash": artifact.provenance.artifact_content_hash,
                },
                sort_keys=True,
            ).encode(),
        )

    response = invoke_suite(base_url, timeout_seconds, request, suite_id, retain_scenario)
    primary = response["primary"]
    if not request.publish:
        return {
            "status": "collected",
            "suite_id": str(response["suite"]["suite_id"]),
            "primary_source": str(response["suite"].get("primary_source", "new_discovery")),
            "scenario_count": str(len(response["scenarios"])),
        }
    result = {
        "status": primary.get("status"),
        "run_id": primary.get("run_id"),
        "evidence_manifest": primary.get("evidence_manifest"),
        "artifact": response["artifact"],
    }
    artifact = validate_result(result)
    if artifact.capability.id != request.expected_capability_id:
        raise RuntimeError(
            "discovered capability ID does not match the configured capture contract"
        )
    if artifact.capability.risk.value != request.expected_risk:
        raise RuntimeError(
            "discovered capability risk does not match the configured capture contract"
        )
    if set(artifact.inputs.required) != set(request.expected_inputs):
        raise RuntimeError(
            "discovered input contract does not match the configured capture contract"
        )
    if set(artifact.outputs.required) != set(request.expected_outputs):
        raise RuntimeError(
            "discovered output contract does not match the configured capture contract"
        )
    supported = set(artifact.compatibility.supported_variants)
    required_tenants = {request.tenant, *request.validation_tenants}
    if not required_tenants.issubset(supported):
        raise RuntimeError("published artifact omitted a validated tenant")
    write_new_artifact(artifact_output, artifact)
    return {
        "artifact_content_hash": artifact.provenance.artifact_content_hash or "",
        "artifact_output": str(artifact_output),
        "artifact_provenance_manifest": artifact.provenance.evidence_manifest_key,
        "capability_id": artifact.capability.id,
        "evidence_manifest": str(result["evidence_manifest"]),
        "model": artifact.provenance.model,
        "run_id": str(result["run_id"]),
        "status": str(result["status"]),
        "suite_id": str(response["suite"]["suite_id"]),
        "primary_source": str(response["suite"].get("primary_source", "new_discovery")),
        "version": artifact.capability.version,
    }
