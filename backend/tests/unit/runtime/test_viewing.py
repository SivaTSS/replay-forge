"""Injected orchestration tests, not provider-backed discovery evidence."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from replayforge.api.contracts import LaunchRequest
from replayforge.discovery.models import DiscoverySuccess
from replayforge.runs.discovery_suite import DiscoverySuiteError, DiscoverySuiteService
from replayforge.runs.results import FailureResult
from replayforge.runs.service import ReplayApplicationService
from replayforge.runs.viewing import current_execution
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings


@pytest.mark.parametrize("failure", [None, "primary", "validation"])
def test_discovery_view_uses_real_pipeline_order_without_human_approval(
    tmp_path: Path,
    failure: str | None,
) -> None:
    runtime = build_runtime(
        RuntimeSettings(  # type: ignore[call-arg]  # Pydantic runtime dotenv override.
            _env_file=None,
            evidence_directory=tmp_path,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    try:
        controller = runtime.execution_controller
        assert controller is not None
        record = controller.registry.all()[0]
        run_id = "run_" + "a" * 32
        primary: Any = DiscoverySuccess(
            "success", run_id, record.artifact, f"evidence://{run_id}/manifest.json"
        )
        if failure == "primary":
            primary = FailureResult(
                status="failure",
                run_id=run_id,
                code="low_model_confidence",
                message="Synthetic blocked discovery",
                recoverable=False,
                evidence_manifest=f"evidence://{run_id}/manifest.json",
            )
        suite = SimpleNamespace(primary=primary, suite_id="sui_" + "b" * 32)
        phases = []
        mock = Mock()
        mock.ready.return_value = True

        def phase(*args: Any, **kwargs: Any) -> Any:
            feed = current_execution.get()
            assert feed is not None
            phases.append(feed.bind(run_id).phase)
            return suite

        mock.create.side_effect = phase
        mock.validate.side_effect = (
            DiscoverySuiteError("private diagnostic") if failure == "validation" else phase
        )
        mock.finalize.side_effect = phase
        mock.published_artifact.return_value = record.artifact
        controller = replace(controller, suites=cast(DiscoverySuiteService, mock))
        request = LaunchRequest.model_validate(
            {
                "execution": {
                    "mode": "discovery",
                    "goal": "Find a synthetic account value",
                    "tenant": "harbor",
                    "application_family": "northstar_member_service",
                    "entry_point": "legacy_servicing",
                    "inputs": {},
                    "validation_tenants": ["summit", "harbor"],
                }
            }
        )
        started = controller.start(request)
        controller.viewer._pool.shutdown(wait=True)
        state = controller.viewer.snapshot(started["execution_id"], started["viewer_token"])
        assert "private diagnostic" not in str(state)
        if failure is None:
            assert phases == ["discovery", "validation:summit", "final-validation:harbor"]
            mock.validate.assert_called_once_with(suite.suite_id, tenant="summit", inputs={})
            mock.finalize.assert_called_once_with(suite.suite_id)
            assert state["phase"] == "publication"
            assert state["state"] == "success"
            assert state["result"]["capability"]["id"] == record.artifact.capability.id
        else:
            assert state["state"] == "failure"
            mock.finalize.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("version", [None, "1.0.1"])
def test_replay_launch_pins_version_and_passes_changed_inputs(
    tmp_path: Path, version: str | None
) -> None:
    runtime = build_runtime(
        RuntimeSettings(  # type: ignore[call-arg]  # Pydantic runtime dotenv override.
            _env_file=None,
            evidence_directory=tmp_path,
            openai_api_key=None,
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )
    )
    try:
        controller = runtime.execution_controller
        assert controller is not None
        mock = Mock()
        mock.invoke.return_value.model_dump.return_value = {"status": "success"}
        controller = replace(controller, replay=cast(ReplayApplicationService, mock))
        inputs = {"member_id": "12346", "payoff_date": "2026-09-21"}
        request = LaunchRequest.model_validate(
            {
                "execution": {
                    "mode": "replay",
                    "capability_id": "member.servicing_loan_payoff_quote",
                    "tenant": "harbor",
                    "version": version,
                    "inputs": inputs,
                }
            }
        )
        started = controller.start(request)
        controller.viewer._pool.shutdown(wait=True)
        mock.invoke.assert_called_once_with(
            "member.servicing_loan_payoff_quote", "1.0.1", "harbor", inputs
        )
        assert (
            controller.viewer.snapshot(started["execution_id"], started["viewer_token"])["state"]
            == "success"
        )
    finally:
        runtime.close()
