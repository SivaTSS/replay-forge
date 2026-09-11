from dataclasses import replace
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

    with pytest.raises(ValidationError, match="model policy file"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            model_policy_file=Path("missing-model-policy.yaml"),
        )


def test_secret_setting_is_masked_and_unconfigured_discovery_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = RuntimeSettings(artifact_directory=artifact_directory())

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
        )

    with pytest.raises(ValidationError, match="configured together"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            langfuse_public_key=SecretStr("local-public-key"),
        )

    with pytest.raises(ValidationError, match="local HTTP origin"):
        RuntimeSettings(
            artifact_directory=artifact_directory(),
            langfuse_base_url="https://cloud.langfuse.com",
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
    registry = load_registry(artifact_directory())
    record = registry.get("member.lookup_savings_balance", "2.0.0")

    policy = effective_replay_policy(record, "http://127.0.0.1:3001")

    assert record.artifact.capability.risk.value == "sensitive"
    assert record.artifact.steps[1].id == "search.submit"
    assert record.artifact.steps[1].risk.value == "sensitive"
    assert policy.maximum_risk.value == "sensitive"


def test_empty_registry_directory_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no versioned"):
        load_registry(tmp_path)

    with pytest.raises(CapabilityNotFoundError):
        load_registry(artifact_directory()).get("missing.capability", "1.0.0")
