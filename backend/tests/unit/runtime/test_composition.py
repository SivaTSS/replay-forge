from dataclasses import replace
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from pydantic import SecretStr, ValidationError

from replayforge.capabilities.registry import CapabilityNotFoundError, InMemoryCapabilityRegistry
from replayforge.runtime.composition import (
    build_runtime,
    effective_replay_policy,
    load_registry,
    origin_ready,
)
from replayforge.runtime.settings import RuntimeSettings
from replayforge.shared.clock import SystemClock
from tests.artifacts import sample_artifact


def artifact_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures/catalog"


def test_settings_validate_origin_and_artifact_directory() -> None:
    settings = RuntimeSettings(
        artifact_directory=artifact_directory(),
        demo_base_url="http://127.0.0.1:3001/",
    )

    assert settings.demo_base_url == "http://127.0.0.1:3001"

    with pytest.raises(ValidationError, match="credential-free"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            demo_base_url="http://user:secret@127.0.0.1:3001",
        )

    with pytest.raises(ValidationError, match="model policy file"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            model_policy_file=Path("missing-model-policy.yaml"),
        )


def test_runtime_disables_unclassified_asset_capture_by_default() -> None:
    assert RuntimeSettings().allow_synthetic_asset_capture is False
    with pytest.raises(ValidationError, match="loopback"):
        RuntimeSettings(
            allow_synthetic_asset_capture=True, demo_base_url="https://bank.example.invalid"
        )
    assert RuntimeSettings(allow_synthetic_asset_capture=True).allow_synthetic_asset_capture is True


def test_synthetic_capture_rejects_remote_registered_origin() -> None:
    with patch("replayforge.runtime.composition.load_application_registry") as registry:
        registry.return_value.all.return_value = (
            SimpleNamespace(origin="https://bank.example.invalid"),
        )
        with pytest.raises(ValueError, match="loopback application"):
            build_runtime(RuntimeSettings(allow_synthetic_asset_capture=True))


@pytest.mark.parametrize(
    ("width", "height", "scale"),
    [
        (799, 800, 1.0),
        (2561, 800, 1.0),
        (1280, 499, 1.0),
        (1280, 1601, 1.0),
        (1280, 800, 0.99),
        (1280, 800, 3.01),
    ],
)
def test_settings_reject_out_of_bounds_browser_viewport(
    width: int, height: int, scale: float
) -> None:
    with pytest.raises(ValidationError, match="browser viewport|device scale"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            browser_viewport_width=width,
            browser_viewport_height=height,
            browser_device_scale_factor=scale,
        )


def test_secret_setting_is_masked_and_unconfigured_discovery_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = RuntimeSettings(
        artifact_directory=artifact_directory(),
        openai_api_key=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )

    assert "runtime-only-key" not in repr(
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            openai_api_key=SecretStr("runtime-only-key"),
            langfuse_public_key=SecretStr("local-public-key"),
            langfuse_secret_key=SecretStr("local-secret-key"),
        )
    )
    monkeypatch.setenv("REPLAYFORGE_OPENAI_MODEL", "gpt-6-astra")
    assert RuntimeSettings(artifact_directory=artifact_directory()).model_policy.model == (
        "gpt-5.6-luna"
    )

    with pytest.raises(ValidationError, match="requires local Langfuse"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            openai_api_key=SecretStr("runtime-only-key"),
            langfuse_public_key=None,
            langfuse_secret_key=None,
        )

    with pytest.raises(ValidationError, match="configured together"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            langfuse_public_key=SecretStr("local-public-key"),
            langfuse_secret_key=None,
        )

    with pytest.raises(ValidationError, match="local HTTP origin"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            langfuse_base_url="https://cloud.langfuse.com",
        )
    runtime = build_runtime(settings)
    try:
        assert not runtime.discovery_service.ready()
        assert runtime.api_services.discovery_invoker is runtime.discovery_service
    finally:
        runtime.close()


def test_registry_loads_reviewed_artifact_and_policy_intersects_five_layers() -> None:
    registry = load_registry(artifact_directory())
    record = registry.get("member.lookup_savings_balance", "1.0.0")

    policy = effective_replay_policy(record, "http://127.0.0.1:3001")

    assert policy.layer_names == ("platform", "application", "tenant", "capability", "invocation")
    assert policy.allowed_action_types == frozenset({"type", "click", "extract"})
    assert policy.maximum_risk.value == "read_only"


def test_runtime_policy_supports_capability_scoped_select_actions() -> None:
    registry = load_registry(artifact_directory())
    record = registry.get("member.lookup_savings_balance", "1.0.0")
    artifact = record.artifact
    widened_actions = frozenset({*artifact.policy.allowed_action_types, "select"})
    record = replace(
        record,
        artifact=artifact.model_copy(
            update={
                "policy": artifact.policy.model_copy(
                    update={"allowed_action_types": widened_actions}
                )
            }
        ),
    )

    policy = effective_replay_policy(record, "http://127.0.0.1:3001")

    assert "select" in policy.allowed_action_types


def test_registry_loads_immutable_handoff_version_with_sensitive_submit() -> None:
    registry = InMemoryCapabilityRegistry(SystemClock())
    record = registry.publish(sample_artifact(sensitive=True))

    policy = effective_replay_policy(record, "http://127.0.0.1:3001")

    assert record.artifact.capability.risk.value == "sensitive"
    assert record.artifact.steps[1].id == "search.submit"
    assert record.artifact.steps[1].risk.value == "sensitive"
    assert policy.maximum_risk.value == "sensitive"


def test_empty_registry_can_bootstrap_first_discovery(tmp_path: Path) -> None:
    registry = load_registry(tmp_path)
    with pytest.raises(CapabilityNotFoundError):
        registry.get("missing.capability", "1.0.0")

    with pytest.raises(CapabilityNotFoundError):
        load_registry(artifact_directory()).get("missing.capability", "1.0.0")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPError("http://target", 404, "not found", Message(), BytesIO()), True),
        (HTTPError("http://target", 503, "unavailable", Message(), BytesIO()), False),
        (URLError("connection refused"), False),
    ],
)
def test_origin_readiness_distinguishes_reachable_client_errors_from_outages(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: bool
) -> None:
    def fail(_origin: str, timeout: int) -> None:
        assert timeout == 1
        raise error

    monkeypatch.setattr("replayforge.runtime.composition.urlopen", fail)

    assert origin_ready("http://target") is expected


def test_runtime_injects_required_local_telemetry_into_openai_provider() -> None:
    settings = RuntimeSettings(
        artifact_directory=artifact_directory(),
        openai_api_key=SecretStr("runtime-only-key"),
        langfuse_public_key=SecretStr("local-public-key"),
        langfuse_secret_key=SecretStr("local-secret-key"),
    )

    with (
        patch("replayforge.runtime.composition.LangfuseModelCallTelemetry.create") as create,
        patch("replayforge.runtime.composition.OpenAIModelProvider.from_api_key") as from_api_key,
    ):
        monitor = create.return_value
        runtime = build_runtime(settings)
        try:
            from_api_key.assert_called_once_with("runtime-only-key", settings.model_policy, monitor)
        finally:
            runtime.close()
