"""Safe, repeatable provisioning for local Langfuse and application environment files."""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

_ASSIGNMENT = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")
_PLACEHOLDER = re.compile(r"^<.*>$")


def _read_assignments(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"environment path must be a regular file: {path}")
    assignments: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        matched = _ASSIGNMENT.fullmatch(line.strip())
        if matched is not None:
            assignments[matched.group("key")] = matched.group("value")
    return assignments


def _configured(value: str | None) -> bool:
    return value is not None and bool(value) and _PLACEHOLDER.fullmatch(value) is None


def _write_env(path: Path, updates: dict[str, str]) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ValueError(f"environment path must be a regular file: {path}")
    original = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    rendered: list[str] = []
    for line in original:
        matched = _ASSIGNMENT.fullmatch(line.strip())
        key = matched.group("key") if matched is not None else None
        rendered.append(f"{key}={remaining.pop(key)}" if key in remaining else line)
    if rendered and rendered[-1]:
        rendered.append("")
    rendered.extend(f"{key}={value}" for key, value in remaining.items())
    payload = "\n".join(rendered) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def provision(application_env: Path, server_env: Path) -> bool:
    """Create credentials once, synchronize both files, and return whether they were new."""
    application = _read_assignments(application_env)
    server = _read_assignments(server_env)
    public_key = server.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY") or application.get(
        "REPLAYFORGE_LANGFUSE_PUBLIC_KEY"
    )
    secret_key = server.get("LANGFUSE_INIT_PROJECT_SECRET_KEY") or application.get(
        "REPLAYFORGE_LANGFUSE_SECRET_KEY"
    )
    if _configured(public_key) != _configured(secret_key):
        raise ValueError("Langfuse public and secret project keys must be configured together")
    created = not _configured(public_key)
    if created:
        public_key = f"lf_pk_{secrets.token_urlsafe(24)}"
        secret_key = f"lf_sk_{secrets.token_urlsafe(32)}"
    assert public_key is not None and secret_key is not None

    minio_password = server.get("MINIO_ROOT_PASSWORD") or secrets.token_hex(24)
    server_updates = {
        "NEXTAUTH_URL": "http://localhost:3100",
        "NEXTAUTH_SECRET": server.get("NEXTAUTH_SECRET") or secrets.token_urlsafe(48),
        "SALT": server.get("SALT") or secrets.token_urlsafe(32),
        "ENCRYPTION_KEY": server.get("ENCRYPTION_KEY") or secrets.token_hex(32),
        "POSTGRES_PASSWORD": server.get("POSTGRES_PASSWORD") or secrets.token_hex(24),
        "CLICKHOUSE_PASSWORD": server.get("CLICKHOUSE_PASSWORD") or secrets.token_hex(24),
        "REDIS_AUTH": server.get("REDIS_AUTH") or secrets.token_hex(24),
        "MINIO_ROOT_PASSWORD": minio_password,
        "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": server.get(
            "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY"
        )
        or minio_password,
        "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY": server.get(
            "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY"
        )
        or minio_password,
        "LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY": server.get(
            "LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY"
        )
        or secrets.token_hex(24),
        "TELEMETRY_ENABLED": "false",
        "LANGFUSE_IN_APP_AGENT_ENABLED": "false",
        "LANGFUSE_INIT_ORG_ID": "replayforge-local",
        "LANGFUSE_INIT_ORG_NAME": "ReplayForge Local",
        "LANGFUSE_INIT_PROJECT_ID": "replayforge",
        "LANGFUSE_INIT_PROJECT_NAME": "ReplayForge",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": public_key,
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": secret_key,
        "LANGFUSE_INIT_USER_EMAIL": "admin@replayforge.local",
        "LANGFUSE_INIT_USER_NAME": "ReplayForge Admin",
        "LANGFUSE_INIT_USER_PASSWORD": server.get("LANGFUSE_INIT_USER_PASSWORD")
        or secrets.token_urlsafe(32),
    }
    server_updates["DATABASE_URL"] = (
        f"postgresql://postgres:{server_updates['POSTGRES_PASSWORD']}@postgres:5432/postgres"
    )
    _write_env(server_env, server_updates)
    _write_env(
        application_env,
        {
            "REPLAYFORGE_LANGFUSE_BASE_URL": "http://127.0.0.1:3100",
            "REPLAYFORGE_LANGFUSE_PUBLIC_KEY": public_key,
            "REPLAYFORGE_LANGFUSE_SECRET_KEY": secret_key,
        },
    )
    return created
