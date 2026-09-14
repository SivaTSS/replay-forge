"""Real UI tests: current screenshot text locates controls, never application state.

These test the target application, not model discovery or a compiled replay capability.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

from replayforge.surfaces.models import VisualToken
from replayforge.surfaces.vision import RapidOcrTextRecognizer

pytestmark = pytest.mark.integration


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass
class Terminal:
    page: Page
    ocr: RapidOcrTextRecognizer
    last_frame: bytes = b""
    last_tokens: tuple[VisualToken, ...] = ()

    def tokens(self) -> tuple[VisualToken, ...]:
        frame = self.page.screenshot(scale="css")
        if frame != self.last_frame:
            self.last_tokens = self.ocr.recognize(frame)
            self.last_frame = frame
        return self.last_tokens

    def see(self, text: str) -> None:
        observed = normalized(" ".join(token.text for token in self.tokens()))
        if normalized(text) not in observed:
            pytest.fail(f"Expected screenshot text: {text}\nObserved OCR: {observed}")

    def click(
        self, label: str, *, row: str = "", menu: bool = False, action_column: bool = False
    ) -> None:
        tokens = self.tokens()
        matches = [token for token in tokens if normalized(token.text) == normalized(label)]
        if row:
            anchors = [token for token in tokens if normalized(token.text) == normalized(row)]
            assert len(anchors) == 1, (row, [token.text for token in tokens])
            anchor = anchors[0].region
            matches = [
                token
                for token in matches
                if abs(token.region.y + token.region.height / 2 - anchor.y - anchor.height / 2)
                < max(anchor.height, token.region.height)
            ]
            if action_column and matches:
                # The visible action column is right of the status column. Both
                # may say "Open"; resolve within this identified row/current frame.
                matches = [max(matches, key=lambda token: token.region.x)]
        if menu and matches:
            # Pick the navigation-column instance, measured from this frame, not an ordinal row.
            matches = [min(matches, key=lambda token: token.region.x)]
        assert len(matches) == 1, (label, [token.text for token in tokens])
        region = matches[0].region
        self.page.mouse.click(region.x + region.width / 2, region.y + region.height / 2)
        self.page.wait_for_timeout(80)

    def fill(self, label: str, value: str) -> None:
        self.click(label)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.insert_text(value)
        self.page.wait_for_timeout(80)

    def open_member(self, member: str = "12345") -> None:
        self.fill("Member ID / name / city", member)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(80)
        self.click("Open")


@pytest.fixture(scope="module")
def workstation_ocr() -> RapidOcrTextRecognizer:
    return RapidOcrTextRecognizer()


@pytest.fixture
def terminal(
    demo_bank: str, workstation_ocr: RapidOcrTextRecognizer, tmp_path: Path
) -> Iterator[Terminal]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{demo_bank}/harbor/servicing")
        page.locator("canvas").wait_for()
        page.wait_for_timeout(250)
        try:
            yield Terminal(page, workstation_ocr)
        finally:
            page.screenshot(path=str(tmp_path / "final-workstation.png"))
            browser.close()


def test_card_lock_and_inverse_change_only_the_selected_record(terminal: Terminal) -> None:
    terminal.open_member()
    terminal.click("Card maintenance", menu=True)
    terminal.click("Open", row="12345-D1")
    terminal.fill("Maintenance reason", "Member reports misplaced card")
    terminal.click("Review temporary lock")
    terminal.see("REVIEW ONLY")
    terminal.click("Confirm operation")
    terminal.see("Card temporarily locked")
    terminal.see("12345-D1")
    terminal.see("Temporarily locked")
    terminal.click("Card maintenance", menu=True)
    terminal.click("Open", row="12345-D1")
    terminal.fill("Maintenance reason", "Member recovered original card")
    terminal.click("Review unlock")
    terminal.click("Confirm operation")
    terminal.see("Card unlocked")
    terminal.see("Active")
    terminal.click("Activity journal", menu=True)
    terminal.see("HBR-000001")
    terminal.see("HBR-000002")
    assert terminal.page.locator("canvas").text_content() == ""
    assert terminal.page.locator("textarea").input_value() == ""
    assert terminal.page.locator("input, select, button, a").count() == 0


def test_transfer_review_cancel_funds_and_posted_confirmation(terminal: Terminal) -> None:
    terminal.open_member()
    terminal.click("Internal transfers", menu=True)
    terminal.fill("Transfer amount (USD)", "4000")
    terminal.fill("Transfer purpose", "Member allocation request")
    terminal.click("Review transfer")
    terminal.see("insufficient_funds")
    terminal.fill("Transfer amount (USD)", "125.50")
    terminal.click("Review transfer")
    terminal.see("REVIEW ONLY")
    terminal.click("Cancel")
    terminal.click("Activity journal", menu=True)
    terminal.see("No records to display")
    terminal.click("Internal transfers", menu=True)
    terminal.fill("Transfer amount (USD)", "125.50")
    terminal.fill("Transfer purpose", "Member allocation request")
    terminal.click("Review transfer")
    terminal.click("Confirm operation")
    terminal.see("Internal transfer posted")
    terminal.see("$3,869.02")
    terminal.click("Transaction research", menu=True)
    terminal.see("HBR-000001")


def test_permission_dropdown_and_payoff_quote(terminal: Terminal) -> None:
    terminal.open_member()
    terminal.click("Workstation", menu=True)
    terminal.click("Training operator role")
    terminal.click("Inquiry only")
    terminal.click("Apply training role")
    terminal.click("Loan servicing", menu=True)
    terminal.click("Calculate payoff")
    # OCR can misorient the long red denial line. Verify its business effect from
    # the unchanged loan screen and empty journal; domain tests assert the code.
    terminal.see("Loan position")
    assert not any("confirm operation" in normalized(token.text) for token in terminal.tokens())
    terminal.click("Activity journal", menu=True)
    terminal.see("No records to display")
    terminal.click("Workstation", menu=True)
    terminal.click("Training operator role")
    terminal.click("Member servicing")
    terminal.click("Apply training role")
    terminal.click("Loan servicing", menu=True)
    terminal.fill("Payoff date (YYYY-MM-DD)", "2026-09-20")
    terminal.click("Calculate payoff")
    terminal.see("$7,832.25")
    terminal.click("Confirm operation")
    terminal.see("Payoff quote issued")


def test_service_case_resolution(terminal: Terminal) -> None:
    terminal.open_member()
    terminal.click("Service cases", menu=True)
    terminal.click("Open", row="CASE-2104", action_column=True)
    terminal.fill("Resolution note", "Explained the distinct merchant posting dates")
    terminal.click("Review resolution")
    terminal.click("Confirm operation")
    terminal.see("Service case resolved")


@pytest.mark.parametrize(
    "tenant,width,height,dpr", [("harbor", 800, 600, 1), ("summit", 1280, 800, 2)]
)
def test_workstation_layout_search_and_scroll(
    demo_bank: str,
    workstation_ocr: RapidOcrTextRecognizer,
    tenant: str,
    width: int,
    height: int,
    dpr: int,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=dpr
        )
        page.goto(f"{demo_bank}/{tenant}/servicing")
        page.locator("canvas").wait_for()
        page.wait_for_timeout(250)
        terminal = Terminal(page, workstation_ocr)
        terminal.open_member()
        terminal.see("Alex Morgan")
        terminal.click("Card maintenance", menu=True)
        page.keyboard.press("PageDown")
        terminal.see("Expired")
        assert page.locator("canvas").text_content() == ""
        browser.close()
