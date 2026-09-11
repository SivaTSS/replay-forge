"""Environment-backed runtime settings with safe URL and path validation."""

from __future__ import annotations

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from replayforge.runtime.model_policy import ModelPolicy, load_model_policy


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPLAYFORGE_",
        env_file=(".env", ".secrets/openai.env"),
        extra="ignore",
    )

    artifact_directory: Path = Path("capabilities")
    evidence_directory: Path = Path("evidence/runtime")
    demo_base_url: str = "http://127.0.0.1:3001"
    browser_headless: bool = True
    model_policy_file: Path = Path("config/model-policy.yaml")
    openai_api_key: SecretStr | None = None
    _model_policy: ModelPolicy = PrivateAttr()

    @property
    def model_policy(self) -> ModelPolicy:
        return self._model_policy

    @model_validator(mode="after")
    def validate_runtime_boundaries(self) -> Self:
        parsed = urlsplit(self.demo_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("demo base URL must be a credential-free HTTP origin")
        if not self.artifact_directory.is_dir():
            raise ValueError("artifact directory does not exist")
        self._model_policy = load_model_policy(self.model_policy_file)
        self.demo_base_url = self.demo_base_url.rstrip("/")
        return self
