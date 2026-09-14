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
from playwright.sync_api import expect, sync_playwright


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
