"""Environment-backed runtime settings with safe URL and path validation."""

from __future__ import annotations

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from replayforge.runtime.model_policy import ModelPolicy, load_model_policy
from replayforge.runtime.vision_policy import VisionGroundingPolicy, load_vision_policy


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPLAYFORGE_",
        env_file=(".env", ".secrets/openai.env", ".secrets/langfuse-client.env"),
        extra="ignore",
    )

    artifact_directory: Path = Path("capabilities")
    application_registry_file: Path = Path("config/applications.yaml")
    capability_asset_directory: Path = Path("capabilities/_assets")
    evidence_directory: Path = Path("evidence/runtime")
    demo_base_url: str = "http://127.0.0.1:3001"
    browser_headless: bool = True
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 800
    browser_device_scale_factor: float = 1.0
    langfuse_base_url: str = "http://127.0.0.1:3100"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    model_policy_file: Path = Path("config/model-policy.yaml")
    vision_policy_file: Path = Path("config/vision-policy.yaml")
    openai_api_key: SecretStr | None = None
    _model_policy: ModelPolicy = PrivateAttr()
    _vision_policy: VisionGroundingPolicy = PrivateAttr()

    @property
    def model_policy(self) -> ModelPolicy:
        return self._model_policy

    @property
    def vision_policy(self) -> VisionGroundingPolicy:
        return self._vision_policy

    @model_validator(mode="after")
    def validate_runtime_boundaries(self) -> Self:
        if not 800 <= self.browser_viewport_width <= 2560:
            raise ValueError("browser viewport width must be between 800 and 2560")
        if not 500 <= self.browser_viewport_height <= 1600:
            raise ValueError("browser viewport height must be between 500 and 1600")
        if not 1.0 <= self.browser_device_scale_factor <= 3.0:
            raise ValueError("browser device scale factor must be between 1 and 3")
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
        if not self.application_registry_file.is_file():
            raise ValueError("application registry file does not exist")
        langfuse_url = urlsplit(self.langfuse_base_url)
        if (
            langfuse_url.scheme != "http"
            or langfuse_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or langfuse_url.username
            or langfuse_url.password
            or langfuse_url.query
            or langfuse_url.fragment
            or langfuse_url.path not in {"", "/"}
        ):
            raise ValueError("Langfuse base URL must be a credential-free local HTTP origin")
        if (self.langfuse_public_key is None) != (self.langfuse_secret_key is None):
            raise ValueError("Langfuse public and secret keys must be configured together")
        if self.openai_api_key is not None and self.langfuse_public_key is None:
            raise ValueError("OpenAI discovery requires local Langfuse monitoring credentials")
        self._model_policy = load_model_policy(self.model_policy_file)
        self._vision_policy = load_vision_policy(self.vision_policy_file)
        self.demo_base_url = self.demo_base_url.rstrip("/")
        self.langfuse_base_url = self.langfuse_base_url.rstrip("/")
        return self
