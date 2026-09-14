from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from replayforge.applications.models import ApplicationRegistration, EntryPointRegistration
from replayforge.applications.registry import load_application_registry


@pytest.fixture
def registration_data() -> dict[str, Any]:
    registry = load_application_registry(
        Path(__file__).resolve().parents[4] / "config/applications.yaml"
    )
    return registry.get("northstar_member_service").model_dump(mode="json")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenants", ["harbor", "harbor"], "tenants must be unique"),
        ("tenants", ["../escape"], "invalid identifier"),
        ("entry_points", {"bad-name": {"path_template": "/"}}, "identifiers"),
        ("surface_contract", "desktop.v1", "not supported"),
        ("route_aliases", {"/same": "/same"}, "must not map a route to itself"),
        ("route_aliases", {"relative": "/members/search"}, "absolute paths"),
        ("route_aliases", {"/source": "/not-approved"}, "outside the approved route policy"),
    ],
)
def test_registration_rejects_invalid_identity_and_routing(
    registration_data: dict[str, Any], field: str, value: Any, message: str
) -> None:
    registration_data[field] = value
    with pytest.raises(ValidationError, match=message):
        ApplicationRegistration.model_validate(registration_data)


@pytest.mark.parametrize("route", ["relative", "/members?query", "/../members", "/members//search"])
def test_application_routes_reject_unsafe_patterns(
    registration_data: dict[str, Any], route: str
) -> None:
    registration_data["policy"]["allowed_route_patterns"] = [route]
    with pytest.raises(ValidationError, match="route patterns"):
        ApplicationRegistration.model_validate(registration_data)


@pytest.mark.parametrize("path", ["/{tenant}/{tenant}", "/../entry", "/entry//other"])
def test_entry_templates_reject_repetition_and_traversal(path: str) -> None:
    with pytest.raises(ValidationError):
        EntryPointRegistration(path_template=path)


def test_resolve_rejects_an_unknown_entry_point(registration_data: dict[str, Any]) -> None:
    registration = ApplicationRegistration.model_validate(registration_data)
    with pytest.raises(ValueError, match="entry point is not registered"):
        registration.resolve("harbor", "missing")
