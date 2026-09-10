from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from replayforge.capabilities.models import (
    ClickAction,
    FrameLocator,
    IdentityMatchesCondition,
    InputValue,
    LocatorBundle,
    LocatorCandidate,
    LocatorScope,
    LocatorStrategy,
    OutputValidCondition,
    RouteCondition,
    TypeAction,
)
from replayforge.policy.types import Risk
from replayforge.runs.results import SuccessResult
from replayforge.runtime.composition import build_runtime
from replayforge.runtime.settings import RuntimeSettings
from replayforge.surfaces.playwright import PlaywrightSurfaceDriver

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def demo_bank() -> Iterator[str]:
    repository = Path(__file__).resolve().parents[3]
    app = repository / "apps" / "demo-bank"
    process = subprocess.Popen(
        [
            str(app / "node_modules" / ".bin" / "next"),
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            "3001",
        ],
        cwd=app,
        env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base_url = "http://127.0.0.1:3001"
    try:
        for _ in range(50):
            try:
                with urlopen(f"{base_url}/harbor", timeout=1) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("demo bank did not become ready")
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)


def in_member_frame(target: LocatorBundle) -> LocatorBundle:
    return target.model_copy(
        update={
            "scope": LocatorScope(
                frame_path=(
                    FrameLocator(
                        locator=LocatorCandidate(
                            strategy=LocatorStrategy.TITLE, value="Member operations"
                        )
                    ),
                )
            )
        }
    )


def test_real_iframe_search_and_account_extraction(demo_bank: str, tmp_path: Path) -> None:
    driver = PlaywrightSurfaceDriver(demo_bank)
    session = driver.open("northstar_member_service", "harbor", "member_search")
    try:
        assert session.observe().route == "/members/search"
        assert session.capture_provider_frame().startswith(b"\x89PNG\r\n\x1a\n")
        member_field = in_member_frame(
            LocatorBundle(
                description="Member ID field",
                candidates=(LocatorCandidate(strategy=LocatorStrategy.LABEL, value="Member ID"),),
            )
        )
        member_target = session.resolve(member_field, 5_000)
        assert member_target.registered_risk is Risk.READ_ONLY
        session.execute(
            TypeAction(kind="type", value=InputValue(source="input", path="member_id")),
            member_target,
            {"member_id": "12345"},
        )
        search = in_member_frame(
            LocatorBundle(
                description="Search button",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.ROLE_NAME,
                        role="button",
                        name="Search",
                    ),
                ),
            )
        )
        search_target = session.resolve(search, 5_000)
        assert search_target.registered_risk is Risk.READ_ONLY
        session.execute(ClickAction(kind="click"), search_target, {})
        details = in_member_frame(
            LocatorBundle(
                description="Savings details link",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.ROLE_NAME,
                        role="link",
                        name="View details",
                    ),
                ),
            )
        )
        details_target = session.resolve(details, 5_000)
        assert details_target.registered_risk is Risk.READ_ONLY
        session.execute(ClickAction(kind="click"), details_target, {})
        assert session.wait_until(
            RouteCondition(kind="route", pattern="/accounts/*/details"), {}, {}, 5_000
        )
        balance = in_member_frame(
            LocatorBundle(
                description="Available balance",
                candidates=(
                    LocatorCandidate(
                        strategy=LocatorStrategy.RELATIVE_TEXT,
                        anchor="Available balance",
                        relation="following_value",
                        element="dd",
                    ),
                ),
            )
        )
        assert session.extract(session.resolve(balance, 5_000)) == "$1,420.57"
        outputs = {"member_id": "12345"}
        inputs = {"member_id": "12345"}
        assert session.evaluate(
            OutputValidCondition(kind="output_valid", output="member_id"), outputs, inputs
        )
        assert session.evaluate(
            IdentityMatchesCondition(
                kind="identity_matches",
                extracted_output="member_id",
                input_path="member_id",
            ),
            outputs,
            inputs,
        )
        screenshot = tmp_path / "account-details.png"
        session.screenshot(screenshot)
        assert screenshot.stat().st_size > 1_000
    finally:
        session.close()
        driver.close()


def test_registered_artifact_replays_end_to_end(demo_bank: str) -> None:
    repository = Path(__file__).resolve().parents[3]
    runtime = build_runtime(
        RuntimeSettings(
            artifact_directory=repository / "capabilities",
            demo_base_url=demo_bank,
        )
    )
    try:
        result = runtime.service.invoke(
            "member.lookup_savings_balance",
            "1.0.0",
            "harbor",
            {"member_id": "12345"},
        )

        assert isinstance(result, SuccessResult)
        assert result.outputs == {
            "member_id": "12345",
            "account_type": "savings",
            "currency": "USD",
            "available_balance": "1420.57",
            "as_of": "2026-09-10T12:30:00Z",
        }
        assert result.checkpoint.verified
        event_types = [event.event_type for event in runtime.journals[result.run_id].events()]
        assert event_types[0] == "replay_started"
        assert event_types[-1] == "checkpoint_verified"
        assert runtime.live_drivers == {}
    finally:
        runtime.close()
