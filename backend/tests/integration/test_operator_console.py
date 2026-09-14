from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from replayforge.surfaces.vision import RapidOcrTextRecognizer
from tests.integration.test_playwright_surface import handoff_fixture


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
    with urlopen(request, timeout=120) as response:
        parsed = json.loads(response.read())
    assert isinstance(parsed, dict)
    return parsed


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(frozen=True)
class OperatorStack:
    evidence: Path
    api_url: str
    ui_url: str

    def forward(self, route: Route) -> None:
        # Real HTTP forwarding to the isolated runtime, not mocked responses.
        path = route.request.url.split("/runtime/", 1)[1]
        response = route.fetch(url=f"{self.api_url}/{path}", timeout=120_000)
        route.fulfill(response=response)


@pytest.fixture(scope="module")
def operator_stack(
    tmp_path_factory: pytest.TempPathFactory, demo_bank: str
) -> Iterator[OperatorStack]:
    repository = Path(__file__).resolve().parents[3]
    root = tmp_path_factory.mktemp("operator-stack")
    catalog = handoff_fixture(root)
    api_port, ui_port = _free_port(), _free_port()
    stack = OperatorStack(
        root / "evidence", f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{ui_port}"
    )
    environment = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
    processes: list[subprocess.Popen[bytes]] = []
    with (root / "services.log").open("wb") as log:
        try:
            # Construct settings explicitly: no inherited provider credentials or dotenv.
            code = (
                "import uvicorn, atexit; "
                "from replayforge.api.app import create_app; "
                "from replayforge.runtime.composition import build_runtime; "
                "from replayforge.runtime.settings import RuntimeSettings; "
                "from pathlib import Path; "
                f"runtime=build_runtime(RuntimeSettings(_env_file=None, "
                f"artifact_directory=Path({str(catalog)!r}), "
                f"evidence_directory=Path({str(stack.evidence)!r}), "
                f"demo_base_url={demo_bank!r}, openai_api_key=None, "
                "langfuse_public_key=None, langfuse_secret_key=None)); "
                "atexit.register(runtime.close); "
                f"uvicorn.run(create_app(runtime.api_services),host='127.0.0.1',port={api_port})"
            )
            processes.append(
                subprocess.Popen(
                    [str(repository / ".venv/bin/python"), "-c", code],
                    cwd=repository,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
            _wait(f"{stack.api_url}/health/live", processes[-1])
            processes.append(
                subprocess.Popen(
                    [
                        str(repository / "apps/control-plane/node_modules/.bin/next"),
                        "start",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        str(ui_port),
                    ],
                    cwd=repository / "apps/control-plane",
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
            _wait(stack.ui_url, processes[-1])
            yield stack
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                process.wait(timeout=10)


@pytest.mark.integration
def test_delayed_lookup_cannot_replace_new_operator_selection(
    operator_stack: OperatorStack,
) -> None:
    # Mock only HTTP ordering; this test does not claim to be discovery/handoff evidence.
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
        page.goto(f"{operator_stack.ui_url}/interventions")
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
def test_operator_finds_controls_and_resumes_real_session(operator_stack: OperatorStack) -> None:
    paused = _post(
        f"{operator_stack.api_url}/api/v1/capabilities/member.servicing_loan_payoff_quote/invoke",
        {
            "tenant": "harbor",
            "version": "1.0.1",
            "inputs": {"member_id": "12345", "payoff_date": "2026-09-20"},
        },
    )
    assert paused["status"] == "intervention_required"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/runtime/**", lambda route: operator_stack.forward(route))
        page.goto(f"{operator_stack.ui_url}/interventions")

        queue_item = page.get_by_role("button", name="payoff", exact=False).first
        expect(queue_item).to_be_visible()
        queue_item.click()
        page.get_by_role("button", name="Claim control").click()
        expect(page.get_by_text("Exclusive control acquired.")).to_be_visible()
        viewport = page.get_by_alt_text(
            "Current retained browser viewport; click to send a left-click"
        )
        expect(viewport).to_be_visible()

        # Recognize the actual live PNG, not the console's downscaled thumbnail.
        # This models a human choosing an observed target; no saved coordinates.
        frame = bytes(
            viewport.evaluate(
                "async image => Array.from(new Uint8Array("
                "await (await fetch(image.src)).arrayBuffer()))"
            )
        )
        tokens = RapidOcrTextRecognizer().recognize(frame)
        matches = [token for token in tokens if token.text.strip() == "Open"]
        assert len(matches) == 1
        region = matches[0].region
        dimensions = viewport.evaluate("image => [image.naturalWidth, image.naturalHeight]")
        displayed = viewport.bounding_box()
        assert displayed is not None
        viewport.click(
            position={
                "x": (region.x + region.width / 2) * displayed["width"] / dimensions[0],
                "y": (region.y + region.height / 2) * displayed["height"] / dimensions[1],
            }
        )
        expect(page.get_by_text("Input applied to the retained session.")).to_be_visible()
        page.get_by_role("button", name="Resume automation").click()
        expect(page.get_by_text("Replay result: success", exact=True)).to_be_visible(timeout=60_000)
        expect(page.get_by_text("No replay sessions need an operator.")).to_be_visible()
        browser.close()

    events = "\n".join(
        path.read_text() for path in operator_stack.evidence.rglob("run-event-*.bin")
    )
    assert "human_input_applied" in events
    assert "automation_resumed" in events


@pytest.mark.integration
def test_watch_replay_history_reconnect_and_same_session_handoff(
    operator_stack: OperatorStack,
) -> None:
    """Real managed replay; only its sensitive policy boundary is an injected fixture."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.route("**/runtime/**", lambda route: operator_stack.forward(route))
        page.goto(operator_stack.ui_url)
        page.get_by_role("button", name="Use demo defaults").click()
        page.get_by_label("member id", exact=False).fill("12346")
        page.get_by_label("payoff date", exact=False).fill("2026-09-21")
        page.get_by_role("button", name="Run and watch", exact=True).click()
        expect(page.locator(".execution-id")).to_contain_text("exe_")
        execution_id = page.locator(".execution-id").inner_text()
        expect(page.get_by_role("button", name="Back", exact=True)).to_be_enabled(timeout=90_000)
        page.get_by_role("button", name="Back", exact=True).click()
        expect(page.get_by_alt_text("Earlier replay screen, read-only")).to_be_visible()
        expect(page.get_by_role("button", name="Claim control")).not_to_be_visible()
        page.get_by_role("button", name="Live", exact=True).click()
        expect(page.get_by_role("button", name="Claim control")).to_be_visible(timeout=90_000)

        page.reload()
        expect(page.locator(".execution-id")).to_have_text(execution_id)
        page.get_by_role("button", name="Claim control").click()
        viewport = page.get_by_alt_text(
            "Current retained browser viewport; click to send a left-click"
        )
        expect(viewport).to_be_visible()
        frame = bytes(
            viewport.evaluate(
                "async image => Array.from(new Uint8Array("
                "await (await fetch(image.src)).arrayBuffer()))"
            )
        )
        matches = [
            token
            for token in RapidOcrTextRecognizer().recognize(frame)
            if token.text.strip() == "Open"
        ]
        assert len(matches) == 1
        region = matches[0].region
        dimensions = viewport.evaluate("image => [image.naturalWidth, image.naturalHeight]")
        displayed = viewport.bounding_box()
        assert displayed is not None
        viewport.click(
            position={
                "x": (region.x + region.width / 2) * displayed["width"] / dimensions[0],
                "y": (region.y + region.height / 2) * displayed["height"] / dimensions[1],
            }
        )
        expect(page.get_by_text("Input applied to the retained session.")).to_be_visible()
        page.get_by_role("button", name="Resume automation").click()
        page.get_by_role("button", name="Back", exact=True).click()
        expect(page.get_by_alt_text("Earlier replay screen, read-only")).to_be_visible()
        previous = page.get_by_alt_text("Earlier replay screen, read-only").get_attribute("src")
        result = page.get_by_role("region", name="Final result")
        expect(result).to_contain_text('"status": "success"', timeout=180_000)
        expect(result).to_contain_text("$9,035.70")
        expect(result).to_contain_text("2026-09-21")
        expect(page.get_by_alt_text("Earlier replay screen, read-only")).to_have_attribute(
            "src", previous or ""
        )
        page.get_by_role("button", name="Live", exact=True).click()
        expect(page.get_by_alt_text("Actual execution browser screen, read-only")).to_be_visible()
        expect(page.get_by_text("automation resumed", exact=True)).to_be_visible()
        browser.close()
