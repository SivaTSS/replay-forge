"""Instrumented canvas UI regressions, separate from screenshot/OCR automation proof.

Observe actual drawing calls, not React state or the renderer's private hit map.
Input still travels through real browser pointer/keyboard events. This allows broad,
fast target-app coverage; test_servicing_workstation retains independent OCR checks.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, sync_playwright

pytestmark = pytest.mark.integration

DRAW_PROBE = """(() => {
  const proto = CanvasRenderingContext2D.prototype;
  const clear = proto.clearRect;
  const fill = proto.fillText;
  const fillRect = proto.fillRect;
  const save = proto.save, restore = proto.restore, begin = proto.beginPath;
  const rect = proto.rect, clip = proto.clip;
  const state = new WeakMap();
  const get = ctx => {
    if (!state.has(ctx)) state.set(ctx, {bounds: null, pending: null, stack: []});
    return state.get(ctx);
  };
  window.__testDraws = [];
  window.__testRects = [];
  proto.fillRect = function(x,y,w,h) {
    window.__testRects.push({x,y,w,h}); return fillRect.call(this,x,y,w,h);
  };
  proto.save = function() { const s = get(this); s.stack.push(s.bounds); return save.call(this); };
  proto.restore = function() {
    const s = get(this); s.bounds = s.stack.pop() ?? null; return restore.call(this);
  };
  proto.beginPath = function() { get(this).pending = null; return begin.call(this); };
  proto.rect = function(x,y,w,h) { get(this).pending = {x,y,w,h}; return rect.call(this,x,y,w,h); };
  proto.clip = function(...args) {
    const s = get(this); if(s.pending) s.bounds = s.pending; return clip.apply(this,args);
  };
  proto.clearRect = function(...args) {
    window.__testDraws = [];
    window.__testRects = [];
    state.delete(this);
    return clear.apply(this, args);
  };
  proto.fillText = function(text, x, y, ...rest) {
    const metrics = this.measureText(text);
    const b = get(this).bounds;
    const viewport = this.canvas.getBoundingClientRect();
    const top = y - metrics.actualBoundingBoxAscent;
    const bottom = y + metrics.actualBoundingBoxDescent;
    const visible = (!b || (x >= b.x && x + metrics.width <= b.x+b.w &&
      top >= b.y && bottom <= b.y+b.h)) && viewport.left+x >= 0 &&
      viewport.left+x+metrics.width <= innerWidth && viewport.top+top >= 0 &&
      viewport.top+bottom <= innerHeight;
    window.__testDraws.push({text: String(text), x, y,
      width: metrics.width, height: parseFloat(this.font.match(/[\\d.]+px/)[0]), visible});
    return fill.call(this, text, x, y, ...rest);
  };
})()"""


@dataclass
class CanvasUI:
    page: Page
    tenant: str

    def reference(self, index: int) -> str:
        return f"{'HBR' if self.tenant == 'harbor' else 'SUM'}-{index:06d}"

    def draws(self) -> list[dict[str, Any]]:
        return self.page.evaluate("window.__testDraws.filter(item => item.visible)")  # type: ignore[no-any-return]

    def see(self, text: str) -> None:
        observed = " ".join(item["text"] for item in self.draws())
        assert text.casefold() in observed.casefold(), (text, observed)

    def absent(self, text: str) -> None:
        assert not any(text in item["text"] for item in self.draws()), text

    def settle(self) -> None:
        self.page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )

    def click(self, text: str, *, menu: bool = False, row: str = "") -> None:
        drawn = self.draws()
        matches = [item for item in drawn if item["text"] == text]
        if row:
            anchors = [
                item
                for item in drawn
                if item["text"] == row
                and any(
                    abs(item["y"] - match["y"]) < max(item["height"], match["height"])
                    for match in matches
                )
            ]
            assert len(anchors) == 1, (row, drawn)
            matches = [
                item
                for item in matches
                if abs(item["y"] - anchors[0]["y"]) < max(item["height"], anchors[0]["height"])
            ]
            if matches:
                matches = [max(matches, key=lambda item: item["x"])]
        elif menu and matches:
            matches = [min(matches, key=lambda item: item["x"])]
        assert len(matches) == 1, (text, matches)
        item = matches[0]
        bounds = self.page.locator("canvas").bounding_box()
        assert bounds
        self.page.mouse.click(
            bounds["x"] + item["x"] + item["width"] / 2,
            bounds["y"] + item["y"] - item["height"] / 2,
        )
        self.settle()

    def fill(self, label: str, value: str) -> None:
        self.click(label)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.insert_text(value)
        self.settle()

    def member(self, member_id: str = "12345") -> None:
        self.click("Member inquiry", menu=True)
        self.fill("Member ID / name / city", member_id)
        self.page.keyboard.press("Enter")
        self.settle()
        self.click("Open", row=member_id)

    def role(self, label: str) -> None:
        self.click("Workstation", menu=True)
        self.click("Training operator role")
        self.click(label)
        self.click("Apply training role")

    def scroll_to(self, text: str) -> None:
        for _ in range(12):
            if any(item["text"] == text for item in self.draws()):
                return
            self.page.keyboard.press("PageDown")
            self.settle()
        pytest.fail(f"Text never became visible while scrolling: {text}")


@pytest.fixture(params=["harbor", "summit"])
def ui(demo_bank: str, tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[CanvasUI]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(DRAW_PROBE)
        page.goto(f"{demo_bank}/{request.param}/servicing")
        page.wait_for_function("window.__testDraws.some(d => d.text === 'Branch member directory')")
        try:
            yield CanvasUI(page, request.param)
            assert not errors, errors
        finally:
            page.screenshot(path=str(tmp_path / "final-workstation.png"))
            browser.close()


def test_new_inquiry_clears_previous_member_context(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Member inquiry", menu=True)
    ui.fill("Member ID / name / city", "UNKNOWN")
    ui.click("Search")
    ui.see("No matching members")
    ui.click("Internal transfers", menu=True)
    ui.see("member_required")


def test_cancel_preserves_instructions_for_correction(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Internal transfers", menu=True)
    ui.fill("Transfer amount (USD)", "25.50")
    ui.fill("Transfer purpose", "Reallocate member funds")
    ui.click("Review transfer")
    ui.see("Proposed internal transfer")
    ui.absent("Internal transfer posted")
    ui.click("Cancel")
    ui.see("25.50")
    ui.see("Reallocate member funds")
    ui.click("Activity journal", menu=True)
    ui.see("No records to display")


def test_text_cursor_editing_changes_the_intended_characters(ui: CanvasUI) -> None:
    ui.fill("Member ID / name / city", "12345")
    ui.page.keyboard.press("Home")
    ui.page.keyboard.press("Delete")
    ui.page.keyboard.insert_text("9")
    ui.settle()
    ui.see("92345")
    ui.page.keyboard.press("End")
    ui.page.keyboard.press("Shift+ArrowLeft")
    ui.page.keyboard.insert_text("6")
    ui.settle()
    ui.see("92346")


def test_holds_change_availability_and_release_without_changing_ledger(ui: CanvasUI) -> None:
    ui.member()
    ui.role("Branch supervisor")
    ui.click("Holds / restrictions", menu=True)
    ui.fill("Hold amount (USD)", "20.00")
    ui.fill("Placement / release reason", "Administrative review hold")
    ui.click("Review new hold")
    ui.see("REVIEW ONLY")
    ui.click("Confirm operation")
    ui.see("$3,974.52")
    ui.click("Holds / restrictions", menu=True)
    ui.fill("Placement / release reason", "Administrative review completed")
    ui.click("Release", row=ui.reference(1))
    ui.click("Confirm operation")
    ui.see("$3,994.52")
    ui.click("Account balances", menu=True)
    ui.see("$4,046.62")


def test_create_resolve_and_reject_repeated_case_resolution(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Service cases", menu=True)
    ui.fill("Request summary", "Investigate two similar merchant purchases")
    ui.click("Review new case")
    ui.see("Investigate two similar merchant purchases")
    ui.click("Confirm operation")
    ui.see("Service case opened")
    ui.click("Service cases", menu=True)
    ui.click("Open", row=ui.reference(1))
    ui.fill("Resolution note", "Confirmed purchases on distinct business dates")
    ui.click("Review resolution")
    ui.click("Confirm operation")
    ui.see("Service case resolved")
    ui.click("Service cases", menu=True)
    ui.click("Open", row=ui.reference(1))
    ui.fill("Resolution note", "Attempted second resolution request")
    ui.click("Review resolution")
    ui.see("case_resolved")


def test_transaction_filters_select_the_real_posting(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Transaction research", menu=True)
    ui.fill("Description / reference", "NORTHWIND")
    ui.fill("Start date (YYYY-MM-DD)", "2026-09-09")
    ui.fill("End date (YYYY-MM-DD)", "2026-09-09")
    ui.click("Apply filter")
    ui.click("Open", row="POS-80420")
    ui.see("-$31.62")
    ui.see("2026-09-09")


def test_notice_restriction_and_frozen_account_are_distinct(ui: CanvasUI) -> None:
    ui.member("23456")
    ui.click("Loan servicing", menu=True)
    ui.click("Calculate payoff")
    ui.see("review_notice")
    ui.click("Relationship summary", menu=True)
    ui.click("Acknowledge notice")
    ui.click("Loan servicing", menu=True)
    ui.click("Calculate payoff")
    ui.see("REVIEW ONLY")
    ui.member("34567")
    ui.click("Acknowledge notice")
    ui.click("Loan servicing", menu=True)
    ui.click("Calculate payoff")
    ui.see("member_restricted")
    ui.member("45678")
    ui.click("Internal transfers", menu=True)
    ui.fill("Transfer amount (USD)", "10")
    ui.fill("Transfer purpose", "Requested member transfer")
    ui.click("Review transfer")
    ui.see("account_frozen")


@pytest.mark.parametrize("width,height", [(800, 600), (1024, 768), (1440, 900)])
def test_resize_long_review_and_confirmation_remain_usable(
    ui: CanvasUI, width: int, height: int
) -> None:
    ui.member()
    ui.click("Internal transfers", menu=True)
    ui.fill("Transfer amount (USD)", "25.50")
    purpose = "X" * 80 + "Y" * 80
    ui.fill("Transfer purpose", purpose)
    ui.page.set_viewport_size({"width": width, "height": height})
    ui.settle()
    # Resize preserves form state; every navigation label remains readable.
    ui.see("Relationship summary")
    ui.see("Transaction research")
    ui.scroll_to("Review transfer")
    ui.click("Review transfer")
    # Inspect the full value across scroll positions, preserving repeated lines.
    pieces: list[str] = []
    for _ in range(8):
        matching = [item for item in ui.draws() if item["text"] and set(item["text"]) <= {"X", "Y"}]
        # At these viewports the entire purpose fits together on at least one frame.
        if len("".join(item["text"] for item in matching)) == len(purpose):
            pieces = [item["text"] for item in matching]
            break
        ui.page.keyboard.press("ArrowDown")
        ui.page.mouse.wheel(0, 80)
        ui.settle()
    assert "".join(pieces) == purpose
    ui.scroll_to("Confirm operation")
    ui.click("Confirm operation")
    ui.see(ui.reference(1))


def test_dropdown_dismissal_does_not_activate_the_covered_screen(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Internal transfers", menu=True)
    ui.fill("Transfer amount (USD)", "25.50")
    ui.fill("Transfer purpose", "Member requested allocation")
    ui.click("Debit account")
    ui.click("Review transfer")
    ui.absent("REVIEW ONLY")
    ui.click("Review transfer")
    ui.see("REVIEW ONLY")


def test_scrollbar_drag_and_keyboard_focus(ui: CanvasUI) -> None:
    ui.member()
    ui.page.set_viewport_size({"width": 800, "height": 600})
    ui.settle()
    track = ui.page.evaluate("""() => window.__testRects
      .filter(r => r.x > innerWidth * .9 && r.h > r.w * 8)
      .sort((a,b) => b.h-a.h)[0]""")
    assert track
    # Drag the actual visible thumb from the top of this newly opened page.
    ui.page.mouse.move(track["x"] + track["w"] / 2, track["y"] + 5)
    ui.page.mouse.down()
    ui.page.mouse.move(track["x"] + track["w"] / 2, track["y"] + track["h"] - 5)
    ui.page.mouse.up()
    ui.settle()
    ui.see("Service request history")
    ui.page.keyboard.press("F2")
    ui.settle()
    ui.fill("Member ID / name / city", "12345")
    ui.page.keyboard.press("Tab")
    ui.page.keyboard.press("Enter")
    ui.settle()
    ui.see("Inquiry results")


def test_reload_and_tabs_have_isolated_training_state(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Internal transfers", menu=True)
    ui.fill("Transfer amount (USD)", "25.50")
    ui.fill("Transfer purpose", "Member requested allocation")
    ui.click("Review transfer")
    ui.click("Confirm operation")
    other = ui.page.context.new_page()
    other.add_init_script(DRAW_PROBE)
    other.goto(ui.page.url)
    other.wait_for_function("window.__testDraws.some(d => d.text === 'Branch member directory')")
    fresh = CanvasUI(other, ui.tenant)
    fresh.click("Activity journal", menu=True)
    fresh.see("No records to display")
    other.close()
    ui.page.reload()
    ui.page.wait_for_function("window.__testDraws.some(d => d.text === 'Branch member directory')")
    ui.click("Activity journal", menu=True)
    ui.see("No records to display")


def test_real_keyboard_copy_cut_paste_and_pointer_caret(ui: CanvasUI) -> None:
    ui.page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    ui.fill("Member ID / name / city", "12345")
    ui.page.keyboard.press("Control+A")
    ui.page.keyboard.press("Control+C")
    assert ui.page.evaluate("navigator.clipboard.readText()") == "12345"
    ui.page.keyboard.press("Control+X")
    ui.settle()
    ui.page.keyboard.press("Control+V")
    ui.settle()
    ui.see("12345")
    # Click the currently painted input value, not a stored input coordinate.
    value = next(item for item in ui.draws() if item["text"] == "12345" and item["height"] == 14)
    ui.page.mouse.click(value["x"] + 1, value["y"] - value["height"] / 2)
    ui.page.keyboard.type("9")
    ui.settle()
    ui.see("912345")
    assert ui.page.locator("textarea").input_value() == ""


def test_card_rejections_and_other_member_identity_remain_visible(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Card maintenance", menu=True)
    ui.click("Open", row="12345-D3")
    ui.fill("Maintenance reason", "Attempt maintenance of expired card")
    ui.click("Review temporary lock")
    ui.see("card_expired")
    ui.click("Card maintenance", menu=True)
    ui.click("Open", row="12345-D2")
    ui.fill("Maintenance reason", "Repeated maintenance request")
    ui.click("Review temporary lock")
    ui.see("already_in_state")
    ui.click("Activity journal", menu=True)
    ui.see("No records to display")
    ui.member("12346")
    ui.click("Card maintenance", menu=True)
    ui.click("Open", row="12346-D1")
    ui.see("Alexandra Morgan")
    ui.see("Active")


def test_invalid_payoff_date_can_be_corrected_without_losing_context(ui: CanvasUI) -> None:
    ui.member()
    ui.click("Loan servicing", menu=True)
    ui.fill("Payoff date (YYYY-MM-DD)", "2026-02-30")
    ui.click("Calculate payoff")
    ui.see("invalid_date")
    ui.fill("Payoff date (YYYY-MM-DD)", "2026-09-20")
    ui.click("Calculate payoff")
    ui.see("$7,832.25")
    ui.click("Confirm operation")
    ui.see("Payoff quote issued")
