from __future__ import annotations

import stat
from pathlib import Path

import pytest

from replayforge.runtime.langfuse_bootstrap import provision


def assignments(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, value in (line.split("=", 1),)
    }


def test_provision_creates_matching_private_credentials_without_losing_existing_env(
    tmp_path: Path,
) -> None:
    application_env = tmp_path / ".env"
    server_env = tmp_path / ".secrets" / "langfuse-server.env"
    application_env.write_text("REPLAYFORGE_OPENAI_API_KEY=preserve-me\n", encoding="utf-8")

    assert provision(application_env, server_env)

    application = assignments(application_env)
    server = assignments(server_env)
    assert application["REPLAYFORGE_OPENAI_API_KEY"] == "preserve-me"
    assert application["REPLAYFORGE_LANGFUSE_PUBLIC_KEY"].startswith("lf_pk_")
    assert application["REPLAYFORGE_LANGFUSE_SECRET_KEY"].startswith("lf_sk_")
    assert (
        application["REPLAYFORGE_LANGFUSE_PUBLIC_KEY"] == server["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
    )
    assert (
        application["REPLAYFORGE_LANGFUSE_SECRET_KEY"] == server["LANGFUSE_INIT_PROJECT_SECRET_KEY"]
    )
    assert stat.S_IMODE(application_env.stat().st_mode) == 0o600
    assert stat.S_IMODE(server_env.stat().st_mode) == 0o600


def test_provision_is_idempotent(tmp_path: Path) -> None:
    application_env = tmp_path / ".env"
    server_env = tmp_path / "server.env"
    provision(application_env, server_env)
    original_application = assignments(application_env)
    original_server = assignments(server_env)

    assert not provision(application_env, server_env)

    assert assignments(application_env) == original_application
    assert assignments(server_env) == original_server


def test_provision_rejects_unpaired_or_non_regular_credentials(tmp_path: Path) -> None:
    application_env = tmp_path / ".env"
    application_env.write_text("REPLAYFORGE_LANGFUSE_PUBLIC_KEY=lf_pk_only\n", encoding="utf-8")

    with pytest.raises(ValueError, match="configured together"):
        provision(application_env, tmp_path / "server.env")

    application_env.unlink()
    application_env.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        provision(application_env, tmp_path / "server.env")
