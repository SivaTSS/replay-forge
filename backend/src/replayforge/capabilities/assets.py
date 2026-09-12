"""Content-addressed visual assets used by deterministic capabilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CapabilityAssetError(ValueError):
    """Raised when an asset reference is invalid or its bytes fail integrity checks."""


class CapabilityAssetStore(Protocol):
    def write(self, content: bytes) -> tuple[str, str]: ...

    def read(self, key: str, content_hash: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class LocalCapabilityAssetStore:
    root: Path
    maximum_bytes: int = 512_000

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, content: bytes) -> tuple[str, str]:
        self._validate_content(content)
        digest = hashlib.sha256(content).hexdigest()
        destination = self.root / f"{digest}.png"
        if destination.exists() and destination.read_bytes() != content:
            raise CapabilityAssetError("content-addressed asset collision")
        if not destination.exists():
            destination.write_bytes(content)
        return f"asset://sha256/{digest}", f"sha256:{digest}"

    def read(self, key: str, content_hash: str) -> bytes:
        prefix = "asset://sha256/"
        if not key.startswith(prefix):
            raise CapabilityAssetError("asset key must be content-addressed")
        digest = key.removeprefix(prefix)
        if content_hash != f"sha256:{digest}" or len(digest) != 64:
            raise CapabilityAssetError("asset key and declared hash disagree")
        try:
            int(digest, 16)
        except ValueError as error:
            raise CapabilityAssetError("asset digest is not hexadecimal") from error
        path = (self.root / f"{digest}.png").resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise CapabilityAssetError("asset key escapes the configured root")
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise CapabilityAssetError("capability asset is missing") from error
        self._validate_content(content)
        if hashlib.sha256(content).hexdigest() != digest:
            raise CapabilityAssetError("capability asset failed integrity verification")
        return content

    def _validate_content(self, content: bytes) -> None:
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CapabilityAssetError("capability assets must be PNG images")
        if len(content) > self.maximum_bytes:
            raise CapabilityAssetError("capability asset exceeds the size limit")
