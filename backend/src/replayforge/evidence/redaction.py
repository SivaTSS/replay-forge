"""Structured redaction and forbidden-content detection before persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from replayforge.evidence.models import SanitizedEvidence
from replayforge.policy.types import DataClassification


class EvidenceRejectedError(ValueError):
    """Raised when evidence cannot be made safe for persistence."""


_FORBIDDEN_KEY_FRAGMENTS = frozenset(
    {"authorization", "cookie", "password", "passwd", "api_key", "token", "secret"}
)
_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}\b", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class StructuredRedactor:
    configured_secrets: tuple[str, ...] = ()
    strict: bool = True
    _minimum_secret_length: int = field(default=8, init=False, repr=False)
    _pseudonym_key: bytes = field(
        default_factory=lambda: secrets.token_bytes(32), init=False, repr=False, compare=False
    )

    def sanitize_json(
        self,
        payload: dict[str, Any],
        classifications: dict[str, DataClassification],
        *,
        run_salt: str,
    ) -> SanitizedEvidence:
        directives: set[str] = set()
        cleaned = self._redact_mapping(
            payload, classifications, run_salt=run_salt, prefix="", directives=directives
        )
        content = json.dumps(
            cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self._scan(content.decode("utf-8"))
        return SanitizedEvidence(
            content=content,
            media_type="application/json",
            redaction_directives=tuple(sorted(directives)),
        )

    def validate_text(self, content: str) -> None:
        """Reject secret-like text that must remain byte-for-byte intact."""

        self._scan(content)

    def _redact_mapping(
        self,
        value: dict[str, Any],
        classifications: dict[str, DataClassification],
        *,
        run_salt: str,
        prefix: str,
        directives: set[str],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./-]{0,255}", key) is None:
                # Dictionary keys and even redaction directives can contain PII.
                directives.add("drop:untrusted-key")
                continue
            normalized_key = key.casefold().replace("-", "_")
            if any(fragment in normalized_key for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                directives.add(f"drop:{path}")
                continue
            classification = classifications.get(path)
            if classification in {
                DataClassification.CREDENTIAL,
                DataClassification.SECRET,
                DataClassification.PERSONAL,
            }:
                directives.add(f"drop:{path}")
                continue
            if classification is DataClassification.CUSTOMER_IDENTIFIER:
                result[key] = self._tokenize(item, run_salt)
                directives.add(f"tokenize:{path}")
                continue
            if classification is DataClassification.FINANCIAL:
                result[key] = "[REDACTED_FINANCIAL]"
                directives.add(f"redact:{path}")
                continue
            if isinstance(item, dict):
                result[key] = self._redact_mapping(
                    item,
                    classifications,
                    run_salt=run_salt,
                    prefix=path,
                    directives=directives,
                )
            elif isinstance(item, list):
                result[key] = [self._redact_sequence_item(entry) for entry in item]
            else:
                result[key] = item
        return result

    @staticmethod
    def _redact_sequence_item(value: Any) -> Any:
        if isinstance(value, str):
            return value
        if isinstance(value, int | float | bool) or value is None:
            return value
        return "[REDACTED_COMPLEX_VALUE]"

    def _tokenize(self, value: Any, run_salt: str) -> str:
        # The public run ID provides separation, not secrecy. The random key never leaves
        # this redactor's lifetime; even a small member-ID space cannot be enumerated from
        # the evidence alone. Canonical JSON also separates strings from numeric values.
        message = json.dumps([run_salt, value], sort_keys=True, separators=(",", ":")).encode()
        digest = hmac.new(self._pseudonym_key, message, hashlib.sha256).hexdigest()[:32]
        return f"customer_{digest}"

    def _scan(self, text: str) -> None:
        findings = [pattern.pattern for pattern in _TOKEN_PATTERNS if pattern.search(text)]
        findings.extend(
            "configured_secret"
            for secret in self.configured_secrets
            if len(secret) >= self._minimum_secret_length and secret in text
        )
        if findings and self.strict:
            raise EvidenceRejectedError("evidence contains forbidden secret-like content")
