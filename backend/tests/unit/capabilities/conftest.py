from copy import deepcopy
from typing import Any

import pytest


@pytest.fixture
def valid_artifact_data() -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "capability": {
            "id": "member.lookup_savings_balance",
            "version": "1.0.0",
            "name": "Lookup savings balance",
            "description": "Return the available balance for a synthetic member.",
            "application_family": "northstar_member_service",
            "surface": "web",
            "risk": "read_only",
            "tags": ["member-service", "read-only"],
        },
        "compatibility": {
            "application_family": "northstar_member_service",
            "base_variant": "standard",
            "supported_variants": ["harbor_credit_union"],
            "surface_contract": "web.v1",
            "entry_point": "member_search",
            "fingerprint": {
                "required_landmarks": [{"kind": "heading", "value": "Member Search"}],
                "forbidden_landmarks": [{"kind": "text", "value": "System maintenance"}],
            },
        },
        "inputs": {
            "type": "object",
            "additional_properties": False,
            "required": ["member_id"],
            "properties": {
                "member_id": {
                    "type": "string",
                    "description": "Synthetic member identifier",
                    "pattern": "^[0-9]{5,10}$",
                    "min_length": 5,
                    "max_length": 10,
                    "data_classification": "customer_identifier",
                    "persistence": "redacted",
                    "example": "12345",
                }
            },
        },
        "outputs": {
            "type": "object",
            "additional_properties": False,
            "required": ["available_balance"],
            "properties": {
                "available_balance": {
                    "type": "string",
                    "description": "Current available balance",
                    "format": "decimal",
                    "pattern": "^-?[0-9]+\\.[0-9]{2}$",
                    "data_classification": "financial",
                    "persistence": "redacted",
                }
            },
        },
        "preconditions": [{"kind": "route", "pattern": "/members/search"}],
        "steps": [
            {
                "id": "search.enter_member_id",
                "name": "Enter member identifier",
                "action": {
                    "kind": "type",
                    "value": {"source": "input", "path": "member_id"},
                },
                "target": {
                    "description": "Member ID field",
                    "candidates": [
                        {"strategy": "label", "value": "Member ID", "expected_count": 1}
                    ],
                    "state": {"visible": True, "enabled": True},
                },
                "postconditions": [],
                "risk": "read_only",
            },
            {
                "id": "search.submit",
                "name": "Submit member search",
                "action": {"kind": "click"},
                "target": {
                    "description": "Search button",
                    "candidates": [
                        {
                            "strategy": "role_name",
                            "role": "button",
                            "name": "Search",
                            "expected_count": 1,
                        }
                    ],
                },
                "postconditions": [],
                "outcome_refs": ["member_not_found"],
                "risk": "read_only",
            },
            {
                "id": "account.extract_balance",
                "name": "Extract available balance",
                "action": {
                    "kind": "extract",
                    "output": "available_balance",
                    "transform": "decimal",
                },
                "target": {
                    "description": "Available balance value",
                    "candidates": [
                        {
                            "strategy": "relative_text",
                            "anchor": "Available balance",
                            "relation": "following_value",
                            "element": "dd",
                            "expected_count": 1,
                        }
                    ],
                },
                "postconditions": [{"kind": "output_valid", "output": "available_balance"}],
                "risk": "read_only",
            },
        ],
        "outcomes": [
            {
                "code": "member_not_found",
                "description": "No matching member exists.",
                "detect": {
                    "kind": "all",
                    "conditions": [
                        {"kind": "text", "value": "No member found"},
                        {"kind": "route", "pattern": "/members/search"},
                    ],
                },
                "allowed_after_steps": ["search.submit"],
                "result": {
                    "status": "business_outcome",
                    "details": {"member_id": {"source": "input", "path": "member_id"}},
                },
            }
        ],
        "checkpoint": {
            "id": "savings_balance_verified",
            "condition": {
                "kind": "all",
                "conditions": [
                    {"kind": "route", "pattern": "/accounts/*/details"},
                    {"kind": "text", "value": "Savings"},
                    {"kind": "output_valid", "output": "available_balance"},
                ],
            },
        },
        "policy": {
            "allowed_action_types": ["type", "click", "extract"],
            "allowed_entry_points": ["member_search"],
            "maximum_risk": "read_only",
            "forbidden_text_inputs": ["password", "security answer"],
        },
        "provenance": {
            "discovery_run_id": "run_0123456789abcdef0123456789abcdef",
            "provider": "test-provider",
            "model": "test-model",
            "prompt_policy_version": "1.0.0",
            "surface_adapter_version": "web.v1",
            "compiler_version": "1.0.0",
            "created_at": "2026-09-10T12:30:00Z",
            "target_fingerprint": "sha256:" + "a" * 64,
            "evidence_manifest_key": "evidence://discovery/example/manifest.json",
        },
    }
    return deepcopy(data)
