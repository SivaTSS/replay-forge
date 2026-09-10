from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from replayforge.capabilities.registry import CapabilityNotFoundError
from replayforge.runtime.composition import effective_replay_policy, load_registry
from replayforge.runtime.settings import RuntimeSettings


def artifact_directory() -> Path:
    return Path(__file__).resolve().parents[4] / "capabilities"


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

    with pytest.raises(ValidationError, match="configured together"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            openai_api_key=SecretStr("runtime-only-key"),
        )


def test_secret_setting_is_masked_and_unconfigured_discovery_is_not_ready() -> None:
    settings = RuntimeSettings(artifact_directory=artifact_directory())

    assert "runtime-only-key" not in repr(
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            openai_api_key=SecretStr("runtime-only-key"),
            openai_model="gpt-test",
        )
    )
    from replayforge.runtime.composition import build_runtime

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


def test_empty_registry_directory_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no versioned"):
        load_registry(tmp_path)

    with pytest.raises(CapabilityNotFoundError):
        load_registry(artifact_directory()).get("missing.capability", "1.0.0")
