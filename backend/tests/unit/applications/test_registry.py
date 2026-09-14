from pathlib import Path

import pytest
from pydantic import ValidationError

from replayforge.applications.models import ApplicationRegistration
from replayforge.applications.registry import load_application_registry


def test_checked_in_catalog_resolves_symbolic_target() -> None:
    registry = load_application_registry(Path("config/applications.yaml"))

    launch = registry.resolve("northstar_member_service", "summit", "visual_member_workbench")

    assert launch.url == "http://127.0.0.1:3001/summit/visual-workbench"
    assert launch.rendered_surface is True
    assert launch.entry_points["member_search"].endswith("/summit")


def test_registration_reads_do_not_expose_mutable_registry_state() -> None:
    registry = load_application_registry(Path("config/applications.yaml"))
    registry.all()[0].entry_points.clear()
    registry.get("northstar_member_service").route_aliases.clear()
    assert registry.all()[0].entry_points
    assert registry.all()[0].route_aliases


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com:invalid",
        "http://example.com:99999",
        "http://example.com?",
        "http://example.com#",
        "http://exam ple.com",
    ],
)
def test_application_origin_rejects_malformed_ports_and_delimiters(origin: str) -> None:
    registration = load_application_registry(Path("config/applications.yaml")).all()[0]
    data = registration.model_dump()
    data["origin"] = origin
    with pytest.raises(ValidationError):
        ApplicationRegistration.model_validate(data)


def test_catalog_rejects_duplicate_yaml_mapping_keys(tmp_path: Path) -> None:
    catalog = tmp_path / "applications.yaml"
    catalog.write_text(
        """schema_version: "1.0"
applications:
  - application_family: safe_app
    application_family: overwritten_app
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="valid safe YAML"):
        load_application_registry(catalog)


def test_registration_rejects_cross_origin_or_unsafe_paths() -> None:
    with pytest.raises(ValidationError):
        ApplicationRegistration.model_validate(
            {
                "application_family": "unsafe_app",
                "capability_namespace": "unsafe",
                "origin": "https://user:password@example.com",
                "surface": "web",
                "surface_contract": "web.v1",
                "base_variant": "standard",
                "tenants": ["tenant"],
                "entry_points": {"home": {"path_template": "/{tenant}/../admin"}},
                "policy": {
                    "allowed_route_patterns": ["/"],
                    "allowed_action_types": ["click"],
                    "maximum_risk": "read_only",
                },
            }
        )


def test_registration_rejects_unknown_template_variables() -> None:
    with pytest.raises(ValidationError, match="tenant variable"):
        ApplicationRegistration.model_validate(
            {
                "application_family": "unsafe_app",
                "capability_namespace": "unsafe",
                "origin": "https://example.com",
                "surface": "web",
                "surface_contract": "web.v1",
                "base_variant": "standard",
                "tenants": ["tenant"],
                "entry_points": {"home": {"path_template": "/{account}"}},
                "policy": {
                    "allowed_route_patterns": ["/"],
                    "allowed_action_types": ["click"],
                    "maximum_risk": "read_only",
                },
            }
        )


def test_registration_rejects_actions_outside_the_web_adapter() -> None:
    with pytest.raises(ValidationError, match="unsupported web actions"):
        ApplicationRegistration.model_validate(
            {
                "application_family": "unsafe_app",
                "capability_namespace": "unsafe",
                "origin": "https://example.com",
                "surface": "web",
                "surface_contract": "web.v1",
                "base_variant": "standard",
                "tenants": ["tenant"],
                "entry_points": {"home": {"path_template": "/{tenant}"}},
                "policy": {
                    "allowed_route_patterns": ["/"],
                    "allowed_action_types": ["click", "execute_shell"],
                    "maximum_risk": "read_only",
                },
            }
        )


def test_registration_cannot_remove_credential_and_secret_forbidden_classes() -> None:
    with pytest.raises(ValidationError, match="must forbid credentials and secrets"):
        ApplicationRegistration.model_validate(
            {
                "application_family": "unsafe_app",
                "capability_namespace": "unsafe",
                "origin": "https://example.com",
                "surface": "web",
                "surface_contract": "web.v1",
                "base_variant": "standard",
                "tenants": ["tenant"],
                "entry_points": {"home": {"path_template": "/{tenant}"}},
                "policy": {
                    "allowed_route_patterns": ["/"],
                    "allowed_action_types": ["click"],
                    "maximum_risk": "read_only",
                    "forbidden_field_classes": [],
                },
            }
        )
