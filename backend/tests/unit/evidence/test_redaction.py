import json

import pytest

from replayforge.evidence.redaction import EvidenceRejectedError, StructuredRedactor
from replayforge.policy.types import DataClassification


def test_structured_redaction_happens_before_serialization() -> None:
    redactor = StructuredRedactor()
    payload = {
        "member_id": "123456789",
        "available_balance": "1420.57",
        "profile": {"full_name": "Synthetic Person", "status": "active"},
        "authorization": "Bearer " + "this-must-never-survive",
        "event": "output_extracted",
    }

    sanitized = redactor.sanitize_json(
        payload,
        {
            "member_id": DataClassification.CUSTOMER_IDENTIFIER,
            "available_balance": DataClassification.FINANCIAL,
            "profile.full_name": DataClassification.PERSONAL,
        },
        run_salt="run-specific-salt",
    )
    stored = json.loads(sanitized.content)

    assert stored["member_id"].startswith("customer_")
    assert stored["available_balance"] == "[REDACTED_FINANCIAL]"
    assert stored["profile"] == {"status": "active"}
    assert "authorization" not in stored
    assert sanitized.redaction_directives == (
        "drop:authorization",
        "drop:profile.full_name",
        "redact:available_balance",
        "tokenize:member_id",
    )


def test_customer_tokens_are_stable_only_within_run_salt() -> None:
    redactor = StructuredRedactor()
    classifications = {"member_id": DataClassification.CUSTOMER_IDENTIFIER}

    first = redactor.sanitize_json({"member_id": "12345"}, classifications, run_salt="run-one")
    repeated = redactor.sanitize_json({"member_id": "12345"}, classifications, run_salt="run-one")
    other_run = redactor.sanitize_json({"member_id": "12345"}, classifications, run_salt="run-two")

    assert first.content == repeated.content
    assert first.content != other_run.content


def test_public_run_id_does_not_make_pseudonyms_reproducible() -> None:
    classifications = {"member_id": DataClassification.CUSTOMER_IDENTIFIER}
    first = StructuredRedactor()
    second = StructuredRedactor()
    assert (
        first.sanitize_json({"member_id": "12345"}, classifications, run_salt="public").content
        != second.sanitize_json({"member_id": "12345"}, classifications, run_salt="public").content
    )
    assert (
        first.sanitize_json({"member_id": "12345"}, classifications, run_salt="public").content
        != first.sanitize_json({"member_id": 12345}, classifications, run_salt="public").content
    )
    assert "pseudonym_key" not in repr(first)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "sk-" + "abcdefghijklmnop1234",
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "ghp_" + "abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN " + "PRIVATE KEY-----",
        "Bearer " + "abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_secret_like_values_are_rejected(unsafe_value: str) -> None:
    with pytest.raises(EvidenceRejectedError, match="forbidden"):
        StructuredRedactor().sanitize_json({"message": unsafe_value}, {}, run_salt="safe-run-salt")


def test_configured_secret_is_rejected_without_disclosing_it() -> None:
    redactor = StructuredRedactor(configured_secrets=("known-sensitive-value",))

    with pytest.raises(EvidenceRejectedError, match="forbidden") as error:
        redactor.sanitize_json(
            {"message": "prefix known-sensitive-value suffix"},
            {},
            run_salt="safe-run-salt",
        )

    assert "known-sensitive-value" not in str(error.value)


def test_validates_secret_free_immutable_text() -> None:
    redactor = StructuredRedactor(configured_secrets=("known-sensitive-value",))

    redactor.validate_text("schema_version: '1.0'")

    with pytest.raises(EvidenceRejectedError, match="forbidden"):
        redactor.validate_text("description: known-sensitive-value")


def test_non_strict_scanner_is_available_only_as_explicit_configuration() -> None:
    sanitized = StructuredRedactor(strict=False).sanitize_json(
        {"synthetic": "sk-" + "abcdefghijklmnop1234"}, {}, run_salt="test"
    )

    assert b"sk-" in sanitized.content


def test_complex_sequence_values_are_conservatively_redacted() -> None:
    sanitized = StructuredRedactor().sanitize_json(
        {"items": [{"possibly_sensitive": "value"}, "safe", 7]},
        {},
        run_salt="test",
    )

    assert json.loads(sanitized.content)["items"] == [
        "[REDACTED_COMPLEX_VALUE]",
        "safe",
        7,
    ]
