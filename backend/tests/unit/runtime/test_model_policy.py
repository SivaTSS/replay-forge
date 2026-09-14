"""Tests for the reviewed model-cost policy boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from replayforge.providers.policy import load_model_policy


def policy_payload() -> dict[str, object]:
    payload = yaml.safe_load(Path("config/model-policy.yaml").read_text())
    assert isinstance(payload, dict)
    return payload


def test_loads_reviewed_cost_sensitive_policy() -> None:
    policy = load_model_policy(Path("config/model-policy.yaml"))

    assert policy.model == "gpt-5.6-luna"
    assert policy.reasoning_effort == "low"
    assert policy.max_model_calls_per_run == 20
    assert policy.max_output_tokens == 1200
    assert str(policy.pricing.input_per_unit) == "0.20"
    assert str(policy.pricing.output_per_unit) == "1.20"


def test_duplicate_model_budget_cannot_override_reviewed_configuration(tmp_path: Path) -> None:
    content = Path("config/model-policy.yaml").read_text()
    path = tmp_path / "duplicate.yaml"
    path.write_text(content + "\nmax_model_calls_per_run: 1\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_model_policy(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"model": "gpt-6-astra"}, "gpt-5.6-luna"),
        ({"reasoning_effort": "max"}, "low"),
        ({"max_model_calls_per_run": 21}, "less than or equal to 20"),
        ({"max_output_tokens": 1_201}, "less than or equal to 1200"),
    ],
)
def test_rejects_unreviewed_or_excessive_policy(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    payload = policy_payload() | mutation
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(payload))

    with pytest.raises(ValidationError, match=message):
        load_model_policy(path)


def test_rejects_non_official_pricing_source(tmp_path: Path) -> None:
    payload = policy_payload()
    pricing = payload["pricing"]
    assert isinstance(pricing, dict)
    pricing["source"] = "https://example.com/pricing"
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(payload))

    with pytest.raises(ValidationError, match="official OpenAI"):
        load_model_policy(path)


@pytest.mark.parametrize("content", ["", "- not-a-mapping\n", "invalid: [yaml"])
def test_rejects_invalid_policy_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(content)

    with pytest.raises(ValueError, match="model policy"):
        load_model_policy(path)


def test_rejects_missing_or_oversized_policy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_model_policy(tmp_path / "missing.yaml")

    oversized = tmp_path / "oversized.yaml"
    oversized.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="exceeds"):
        load_model_policy(oversized)
