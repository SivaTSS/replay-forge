from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest
from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from replayforge.surfaces.models import SurfaceError
from replayforge.surfaces.playwright import PlaywrightSurfaceSession


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


def test_observation_retries_transient_navigation_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    session = session_with(page)
    attempts = 0

    def read_state(
        current: PlaywrightSurfaceSession,
    ) -> tuple[str, tuple[str, ...], object]:
        nonlocal attempts
        del current
        attempts += 1
        if attempts == 1:
            raise PlaywrightError("Execution context was destroyed during navigation")
        return "/members/search", ("Member Search",), "memberNumber"

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
    ) -> tuple[str, tuple[str, ...], object]:
        del current
        raise PlaywrightError("Target page has been closed")

    monkeypatch.setattr(PlaywrightSurfaceSession, "_read_observation_state", fail_state)

    with pytest.raises(SurfaceError, match="could not be observed"):
        session.observe()

    assert page.waits == []
