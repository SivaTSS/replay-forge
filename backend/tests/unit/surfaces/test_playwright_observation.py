from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from replayforge.applications.registry import load_application_registry
from replayforge.capabilities.models import ElementCondition, IdentityMatchesCondition
from replayforge.surfaces.models import ActionableControl, ExtractableField, SurfaceError
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver, PlaywrightSurfaceSession


class FakePage:
    viewport_size: ClassVar[dict[str, int]] = {"width": 1280, "height": 800}

    def __init__(self) -> None:
        self.waits: list[tuple[str, int]] = []

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.waits.append((state, timeout))


def session_with(page: FakePage) -> PlaywrightSurfaceSession:
    return PlaywrightSurfaceSession(
        context=cast(BrowserContext, cast(Any, object())),
        page=cast(Page, cast(Any, page)),
        application_family="northstar_member_service",
        tenant="harbor",
        entry_points={},
    )


def test_driver_requires_explicit_application_registration() -> None:
    with pytest.raises(ValueError, match="application registry"):
        PlaywrightSurfaceDriver("http://127.0.0.1:3001")


def test_rendered_readiness_requires_vision_before_browser_launch() -> None:
    registry = load_application_registry(
        Path(__file__).resolve().parents[4] / "config/applications.yaml"
    )
    driver = PlaywrightSurfaceDriver("http://127.0.0.1:3001", application_registry=registry)
    with pytest.raises(SurfaceError) as error:
        driver.open("northstar_member_service", "harbor", "legacy_servicing")
    assert error.value.code == "vision_not_configured"
    assert driver.playwright is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [("target_absent", True), ("target_ambiguous", False), ("action_failed", False)],
)
def test_absence_requires_proof_not_a_resolution_error(
    monkeypatch: pytest.MonkeyPatch, code: str, expected: bool
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise SurfaceError(code, "Unable to resolve target.")

    monkeypatch.setattr(PlaywrightSurfaceSession, "resolve", fail)
    condition = ElementCondition.model_validate(
        {
            "kind": "element",
            "state": "absent",
            "target": {
                "description": "Notice",
                "candidates": [{"strategy": "text", "value": "Notice"}],
            },
        }
    )
    assert session_with(FakePage()).evaluate(condition, {}, {}) is expected


def test_identity_requires_present_values_and_supports_nested_inputs() -> None:
    condition = IdentityMatchesCondition(
        kind="identity_matches", extracted_output="member_id", input_path="member.id"
    )
    session = session_with(FakePage())
    assert not session.evaluate(condition, {}, {})
    assert session.evaluate(condition, {"member_id": "12345"}, {"member": {"id": "12345"}})


def test_observation_retries_transient_navigation_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    session = session_with(page)
    attempts = 0

    def read_state(
        current: PlaywrightSurfaceSession,
    ) -> tuple[
        str,
        tuple[str, ...],
        tuple[str, ...],
        tuple[ActionableControl, ...],
        tuple[ExtractableField, ...],
        object,
    ]:
        nonlocal attempts
        del current
        attempts += 1
        if attempts == 1:
            raise PlaywrightError("Execution context was destroyed during navigation")
        return "/members/search", ("Member Search",), (), (), (), "memberNumber"

    monkeypatch.setattr(PlaywrightSurfaceSession, "_read_observation_state", read_state)

    observation = session.observe()

    assert observation.route == "/members/search"
    assert observation.landmarks == ("Member Search",)
    assert observation.active_element == "memberNumber"
    assert attempts == 2
    assert page.waits == [("domcontentloaded", 2_000)]


def test_observation_does_not_retry_non_navigation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    session = session_with(page)

    def fail_state(
        current: PlaywrightSurfaceSession,
    ) -> tuple[
        str,
        tuple[str, ...],
        tuple[str, ...],
        tuple[ActionableControl, ...],
        tuple[ExtractableField, ...],
        object,
    ]:
        del current
        raise PlaywrightError("Target page has been closed")

    monkeypatch.setattr(PlaywrightSurfaceSession, "_read_observation_state", fail_state)

    with pytest.raises(SurfaceError, match="could not be observed"):
        session.observe()

    assert page.waits == []
