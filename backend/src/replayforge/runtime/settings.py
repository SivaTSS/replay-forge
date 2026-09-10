"""Environment-backed runtime settings with safe URL and path validation."""

from __future__ import annotations

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPLAYFORGE_", env_file=".env", extra="ignore")

    artifact_directory: Path = Path("capabilities")
    demo_base_url: str = "http://127.0.0.1:3001"
    browser_headless: bool = True
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None

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
        if (self.openai_api_key is None) != (self.openai_model is None):
            raise ValueError("OpenAI API key and model must be configured together")
        if self.openai_model is not None and not self.openai_model.strip():
            raise ValueError("OpenAI model must not be blank")
        self.demo_base_url = self.demo_base_url.rstrip("/")
        return self
