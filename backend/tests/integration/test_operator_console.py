from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Route, expect, sync_playwright


def _wait(url: str, process: subprocess.Popen[bytes]) -> None:
    # Runtime composition loads the OCR model; coverage instrumentation can make
    # cold startup materially slower than the normal local path.
    for _ in range(600):
        if process.poll() is not None:
            raise RuntimeError(f"test service exited before readiness: {url}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.1)
    raise RuntimeError(f"service did not become ready: {url}")


def _post(url: str, body: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        parsed = json.loads(response.read())
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture(scope="module")
def operator_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    repository = Path(__file__).resolve().parents[3]
    evidence = tmp_path_factory.mktemp("operator-evidence")
    environment = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
    log = (evidence / "services.log").open("wb")
    processes = [
        subprocess.Popen(
            [
                str(repository / "apps/demo-bank/node_modules/.bin/next"),
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3001",
            ],
            cwd=repository / "apps/demo-bank",
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    ]
    try:
        _wait("http://127.0.0.1:3001/harbor", processes[-1])
        processes.append(
            subprocess.Popen(
                [
                    str(repository / ".venv/bin/uvicorn"),
                    "replayforge.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                cwd=repository,
                env={
                    **environment,
                    "PYTHONPATH": str(repository / "backend/src"),
                    "REPLAYFORGE_EVIDENCE_DIRECTORY": str(evidence),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        )
        _wait("http://127.0.0.1:8000/health/live", processes[-1])
        processes.append(
            subprocess.Popen(
                [
                    str(repository / "apps/control-plane/node_modules/.bin/next"),
                    "start",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3000",
                ],
                cwd=repository / "apps/control-plane",
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        )
        _wait("http://127.0.0.1:3000", processes[-1])
        yield evidence
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            process.wait(timeout=10)
        log.close()


@pytest.mark.integration
def test_delayed_lookup_cannot_replace_new_operator_selection(operator_stack: Path) -> None:
    # Mock only HTTP ordering; this test does not claim to be discovery/handoff evidence.
    del operator_stack
    first_id, second_id = "int_" + "a" * 32, "int_" + "b" * 32
    records = [
        {
            "intervention_id": identity,
            "capability_name": name,
            "status": "open",
            "control_owner": "automation_paused",
            "lease_version": 1,
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "tenant": "harbor",
            "explanation": "Synthetic ordering test",
        }
        for identity, name in ((first_id, "First task"), (second_id, "Second task"))
    ]
    delayed: list[Route] = []

    def respond(route: Route) -> None:
        if route.request.url.endswith(first_id):
            delayed.append(route)
        elif route.request.url.endswith(second_id):
            route.fulfill(json=records[1])
        else:
            route.fulfill(json={"items": records})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/runtime/api/v1/interventions**", respond)
        page.goto("http://127.0.0.1:3000")
        with page.expect_request(f"**/{first_id}"):
            page.get_by_role("button", name="First task", exact=False).click()
        page.get_by_role("button", name="Second task", exact=False).click()
        expect(page.get_by_role("heading", name="Second task", exact=True)).to_be_visible()
        assert delayed
        with page.expect_response(f"**/{first_id}") as response:
            delayed[0].fulfill(json=records[0])
        response.value.finished()
        page.evaluate(
            "() => new Promise(resolve => "
            "requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        expect(page.get_by_role("heading", name="Second task", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="First task", exact=True)).to_have_count(0)
        browser.close()


@pytest.mark.integration
def test_operator_finds_controls_and_resumes_real_session(operator_stack: Path) -> None:
    paused = _post(
        "http://127.0.0.1:8000/api/v1/capabilities/member.lookup_savings_balance/invoke",
        {"tenant": "harbor", "version": "2.0.0", "inputs": {"member_id": "12345"}},
    )
    assert paused["status"] == "intervention_required"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:3000")

        queue_item = page.get_by_role("button", name="Lookup savings balance", exact=False)
        expect(queue_item).to_be_visible()
        queue_item.click()
        expect(page.get_by_text("search.submit", exact=True)).to_be_visible()
        page.get_by_role("button", name="Claim control").click()
        expect(page.get_by_text("Exclusive control acquired.")).to_be_visible()
        viewport = page.get_by_alt_text(
            "Current retained browser viewport; click to send a left-click"
        )
        expect(viewport).to_be_visible()

        page.get_by_role("button", name="Send key").click()
        expect(page.get_by_text("Input applied to the retained session.")).to_be_visible()
        page.get_by_role("button", name="Resume automation").click()
        expect(page.get_by_text("Replay result: success", exact=True)).to_be_visible(timeout=30_000)
        expect(page.get_by_text("No replay sessions need an operator.")).to_be_visible()
        browser.close()

    events = "\n".join(path.read_text() for path in operator_stack.rglob("run-event-*.bin"))
    assert "human_input_applied" in events
    assert "automation_resumed" in events
