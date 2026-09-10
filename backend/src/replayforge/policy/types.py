"""Stable safety types shared with artifact and execution domains."""

from enum import StrEnum


class Risk(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    SENSITIVE = "sensitive"
    IRREVERSIBLE = "irreversible"


RISK_RANK = {risk: rank for rank, risk in enumerate(Risk)}


class DataClassification(StrEnum):
    PUBLIC = "public"
    OPERATIONAL = "operational"
    CUSTOMER_IDENTIFIER = "customer_identifier"
    PERSONAL = "personal"
    FINANCIAL = "financial"
    CREDENTIAL = "credential"
    SECRET = "secret"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HUMAN_APPROVAL = "require_human_approval"
