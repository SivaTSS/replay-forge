"""Playwright web-surface adapter with strict locator and frame semantics."""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Frame,
    FrameLocator,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from replayforge.capabilities.models import (
    Action,
    AllCondition,
    AnyCondition,
    AssertAction,
    CheckpointAction,
    ClickAction,
    Condition,
    ElementCondition,
    ExtractAction,
    IdentityMatchesCondition,
    InputValue,
    LiteralValue,
    LocatorBundle,
    LocatorCandidate,
    LocatorStrategy,
    MatchMode,
    NavigateAction,
    NotCondition,
    OutputValidCondition,
    PressKeysAction,
    RouteCondition,
    ScrollAction,
    SelectAction,
    SwitchContextAction,
    TextCondition,
    TypeAction,
    WaitForAction,
)
from replayforge.policy.types import Risk
from replayforge.shared.ids import EntityId, EntityKind, new_id
from replayforge.surfaces.models import (
    ActionReceipt,
    ActionStatus,
    HumanInput,
    HumanKeyInput,
    HumanPointerInput,
    HumanTextInput,
    NormalizedObservation,
    ResolvedTarget,
    SanitizedSurfaceFrame,
    SurfaceError,
    SurfaceFrame,
    Viewport,
)

QueryRoot = Page | FrameLocator | Locator
_OBSERVATION_ATTEMPTS = 3
_NAVIGATION_RACE_MARKERS = (
    "execution context was destroyed",
    "cannot find context with specified id",
    "frame was detached",
)
_EVIDENCE_MASK_DIRECTIVES = (
    "mask:form-controls",
    "mask:customer-details",
    "mask:account-table-cells",
)


def _is_navigation_race(error: PlaywrightError) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _NAVIGATION_RACE_MARKERS)


@dataclass(slots=True)
class PlaywrightSurfaceDriver:
    base_url: str
    headless: bool = True
    browser: Browser | None = field(default=None, init=False)
    playwright: Playwright | None = field(default=None, init=False)
    active_session: PlaywrightSurfaceSession | None = field(default=None, init=False)

    def open(
        self, application_family: str, tenant: str, entry_point: str
    ) -> PlaywrightSurfaceSession:
        if application_family != "northstar_member_service":
            raise SurfaceError("unknown_application", "Application family is not registered.")
        if tenant not in {"harbor", "summit"}:
            raise SurfaceError("unknown_tenant", "Tenant is not registered.")
        if entry_point != "member_search":
            raise SurfaceError("unknown_entry_point", "Entry point is not registered.")
        if self.playwright is None:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
        assert self.browser is not None
        context = self.browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        destination = f"{self.base_url.rstrip('/')}/{quote(tenant, safe='')}"
        try:
            page.goto(destination, wait_until="domcontentloaded", timeout=15_000)
            page.frame_locator('iframe[title="Member operations"]').get_by_role(
                "heading", name="Member Search", exact=True
            ).wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError as exc:
            context.close()
            raise SurfaceError(
                "navigation_timeout",
                "The target entry point did not load within its budget.",
                recoverable=True,
                effect_absent=True,
            ) from exc
        session = PlaywrightSurfaceSession(
            context=context,
            page=page,
            application_family=application_family,
            tenant=tenant,
            entry_points={"member_search": destination},
        )
        self.active_session = session
        return session

    def capture_active_frame(self) -> SurfaceFrame:
        if self.active_session is None:
            raise SurfaceError("session_missing", "No active surface session is available.")
        return self.active_session.capture_live_frame()

    def execute_active_human_input(self, action: HumanInput) -> None:
        if self.active_session is None:
            raise SurfaceError("session_missing", "No active surface session is available.")
        self.active_session.execute_human_input(action)

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None
        self.active_session = None


@dataclass(slots=True)
class PlaywrightSurfaceSession:
    context: BrowserContext
    page: Page
    application_family: str
    tenant: str
    entry_points: dict[str, str]
    session_id: EntityId = field(default_factory=lambda: new_id(EntityKind.SESSION))
    _handles: dict[str, Locator] = field(default_factory=dict, init=False)
    _bundles: dict[str, LocatorBundle] = field(default_factory=dict, init=False)

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.page.url)
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"

    def observe(self) -> NormalizedObservation:
        for attempt in range(_OBSERVATION_ATTEMPTS):
            try:
                route, landmarks, frame_titles, active = self._read_observation_state()
                break
            except PlaywrightError as exc:
                if attempt == _OBSERVATION_ATTEMPTS - 1 or not _is_navigation_race(exc):
                    raise SurfaceError(
                        "observation_failed", "The current UI state could not be observed."
                    ) from exc
                with suppress(PlaywrightError):
                    self.page.wait_for_load_state("domcontentloaded", timeout=2_000)
        else:  # pragma: no cover - the bounded loop always returns or raises
            raise AssertionError("observation retry loop exhausted without a result")
        dialog_text = None
        fingerprint_source = "|".join((route, *landmarks, *frame_titles, str(active or "")))
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}
        return NormalizedObservation(
            id=new_id(EntityKind.EVENT),
            session_id=self.session_id,
            captured_at=datetime.now(UTC),
            route=route,
            viewport=Viewport(viewport["width"], viewport["height"]),
            fingerprint=fingerprint,
            landmarks=landmarks,
            frame_titles=frame_titles,
            active_element=str(active) if active else None,
            dialog_text=dialog_text,
        )

    def _read_observation_state(
        self,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...], object]:
        frame = self._application_frame()
        raw_route = urlsplit(frame.url if frame is not None else self.page.url).path
        route = self._normalize_route(raw_route)
        root: Page | Frame = frame or self.page
        landmarks = tuple(
            text.strip()
            for text in root.locator("h1,h2,h3,label,th").all_inner_texts()
            if text.strip()
        )[:40]
        frame_title = frame.frame_element().get_attribute("title") if frame is not None else None
        frame_titles = (frame_title,) if frame_title else ()
        active = root.evaluate(
            "() => document.activeElement?.getAttribute('aria-label') || "
            "document.activeElement?.getAttribute('name') || document.activeElement?.tagName"
        )
        return route, landmarks, frame_titles, active

    def capture_provider_frame(self) -> bytes:
        try:
            return self.page.screenshot(type="png", full_page=False)
        except Exception as exc:
            raise SurfaceError(
                "screenshot_failed", "The current UI frame could not be captured."
            ) from exc

    def capture_sanitized_evidence_frame(self) -> SanitizedSurfaceFrame:
        masks: list[Locator] = []
        for frame in self.page.frames:
            masks.extend(
                (
                    frame.locator("input,textarea,select"),
                    frame.locator("dl dd"),
                    frame.locator(".account-table tbody td"),
                )
            )
        try:
            content = self.page.screenshot(
                type="png",
                full_page=False,
                mask=masks,
                mask_color="#111827",
                animations="disabled",
                caret="hide",
            )
        except Exception as exc:
            raise SurfaceError(
                "evidence_screenshot_failed",
                "A sanitized evidence frame could not be captured.",
            ) from exc
        return SanitizedSurfaceFrame(content, _EVIDENCE_MASK_DIRECTIVES)

    def capture_live_frame(self) -> SurfaceFrame:
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}
        return SurfaceFrame(
            content=self.capture_provider_frame(),
            viewport=Viewport(viewport["width"], viewport["height"]),
        )

    def execute_human_input(self, action: HumanInput) -> None:
        try:
            if isinstance(action, HumanPointerInput):
                viewport = self.page.viewport_size or {"width": 1280, "height": 800}
                if action.x >= viewport["width"] or action.y >= viewport["height"]:
                    raise SurfaceError(
                        "pointer_out_of_bounds",
                        "Pointer coordinates are outside the current viewport.",
                        effect_absent=True,
                    )
                self.page.mouse.click(action.x, action.y, button="left")
            elif isinstance(action, HumanTextInput):
                self.page.keyboard.insert_text(action.text)
            elif isinstance(action, HumanKeyInput):
                self.page.keyboard.press(action.key.value)
            else:
                raise TypeError("unsupported human input action")
        except SurfaceError:
            raise
        except Exception as exc:
            raise SurfaceError(
                "human_input_failed", "The human input could not be applied."
            ) from exc

    def resolve(self, target: LocatorBundle, timeout_ms: int) -> ResolvedTarget:
        root = self._scoped_root(target)
        failures: list[dict[str, object]] = []
        for index, candidate in enumerate(target.candidates):
            if candidate.strategy is LocatorStrategy.COORDINATES:
                failures.append({"candidate": index, "reason": "coordinate_not_stable"})
                continue
            locator = self._locator(root, candidate)
            try:
                locator.first.wait_for(
                    state="attached", timeout=max(100, timeout_ms // len(target.candidates))
                )
            except PlaywrightTimeoutError:
                failures.append({"candidate": index, "count": 0})
                continue
            count = locator.count()
            if count != candidate.expected_count or count != 1:
                failures.append({"candidate": index, "count": count})
                continue
            selected = locator.first
            if target.state.visible and not selected.is_visible():
                failures.append({"candidate": index, "reason": "not_visible"})
                continue
            if target.state.enabled is True and not selected.is_enabled():
                failures.append({"candidate": index, "reason": "not_enabled"})
                continue
            handle = f"target_{uuid4().hex}"
            self._handles[handle] = selected
            self._bundles[handle] = target
            return ResolvedTarget(
                handle=handle,
                description=target.description,
                candidate_index=index,
                observed_count=count,
                registered_risk=self._classify_target(selected),
            )
        ambiguous = any(
            isinstance(item.get("count"), int) and cast(int, item["count"]) > 1 for item in failures
        )
        raise SurfaceError(
            "target_ambiguous" if ambiguous else "target_absent",
            "No locator candidate resolved exactly one actionable control.",
            recoverable=not ambiguous,
            effect_absent=True,
            expected={"count": 1},
            observed={"candidates": failures},
        )

    def capture_locator(self, target: ResolvedTarget) -> LocatorBundle:
        try:
            return self._bundles[target.handle]
        except KeyError as exc:
            raise SurfaceError(
                "target_handle_stale", "Resolved target is no longer available."
            ) from exc

    def execute(
        self, action: Action, target: ResolvedTarget | None, inputs: dict[str, Any]
    ) -> ActionReceipt:
        started = datetime.now(UTC)
        try:
            locator = self._target_locator(target)
            if isinstance(action, NavigateAction):
                destination = self.entry_points.get(action.entry_point)
                if destination is None:
                    raise SurfaceError(
                        "unknown_entry_point", "Navigation entry point is not registered."
                    )
                self.page.goto(destination, wait_until="domcontentloaded", timeout=15_000)
            elif isinstance(action, ClickAction):
                self._required(locator).click()
            elif isinstance(action, TypeAction):
                value = self._resolve_value(action.value, inputs)
                if action.clear:
                    self._required(locator).fill(str(value))
                else:
                    self._required(locator).press_sequentially(str(value))
            elif isinstance(action, PressKeysAction):
                self._required(locator).press("+".join(action.keys))
            elif isinstance(action, SelectAction):
                self._required(locator).select_option(
                    label=str(self._resolve_value(action.option, inputs))
                )
            elif isinstance(action, ScrollAction):
                delta = action.amount if action.direction in {"down", "right"} else -action.amount
                if action.direction in {"up", "down"}:
                    self.page.mouse.wheel(0, delta)
                else:
                    self.page.mouse.wheel(delta, 0)
            elif isinstance(action, WaitForAction | AssertAction | CheckpointAction):
                pass
            elif isinstance(action, SwitchContextAction):
                if action.context != "primary":
                    raise SurfaceError("context_missing", "Requested context is not available.")
            elif isinstance(action, ExtractAction):
                raise SurfaceError(
                    "invalid_action_dispatch", "Extract actions use the extraction port."
                )
            return ActionReceipt(ActionStatus.COMPLETED, started, datetime.now(UTC))
        except SurfaceError:
            raise
        except PlaywrightTimeoutError as exc:
            raise SurfaceError(
                "action_timeout",
                "The UI action exceeded its execution budget.",
                recoverable=True,
            ) from exc
        except Exception as exc:
            raise SurfaceError("action_failed", "The UI action could not be completed.") from exc

    def evaluate(
        self, condition: Condition, outputs: dict[str, Any], inputs: dict[str, Any]
    ) -> bool:
        if isinstance(condition, AllCondition):
            return all(self.evaluate(item, outputs, inputs) for item in condition.conditions)
        if isinstance(condition, AnyCondition):
            return any(self.evaluate(item, outputs, inputs) for item in condition.conditions)
        if isinstance(condition, NotCondition):
            return not self.evaluate(condition.condition, outputs, inputs)
        if isinstance(condition, RouteCondition):
            return self._route_matches(self.observe().route, condition.pattern)
        if isinstance(condition, TextCondition):
            root: Page | Frame = self._application_frame() or self.page
            locator = root.get_by_text(condition.value, exact=condition.match is MatchMode.EXACT)
            return locator.count() > 0
        if isinstance(condition, ElementCondition):
            try:
                resolved = self.resolve(condition.target, 1_000)
                locator = self._handles[resolved.handle]
            except SurfaceError:
                return condition.state in {"absent", "hidden"}
            states = {
                "exists": True,
                "absent": False,
                "visible": locator.is_visible(),
                "hidden": not locator.is_visible(),
                "enabled": locator.is_enabled(),
                "disabled": not locator.is_enabled(),
            }
            return states[condition.state]
        if isinstance(condition, OutputValidCondition):
            return condition.output in outputs and outputs[condition.output] not in {None, ""}
        if isinstance(condition, IdentityMatchesCondition):
            return outputs.get(condition.extracted_output) == inputs.get(condition.input_path)
        return True

    def extract(self, target: ResolvedTarget) -> str:
        return self._target_locator(target).inner_text().strip()

    def wait_until(
        self,
        condition: Condition,
        outputs: dict[str, Any],
        inputs: dict[str, Any],
        timeout_ms: int,
    ) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self.evaluate(condition, outputs, inputs):
                return True
            self.page.wait_for_timeout(50)
        return self.evaluate(condition, outputs, inputs)

    def screenshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(destination), full_page=True)

    def close(self) -> None:
        self.context.close()

    def _application_frame(self) -> Frame | None:
        return next(
            (frame for frame in self.page.frames if frame is not self.page.main_frame), None
        )

    def _normalize_route(self, route: str) -> str:
        prefix = f"/{self.tenant}"
        if route.startswith(prefix):
            route = route.removeprefix(prefix) or "/"
        if route in {"/member-search", "/member-results"}:
            return "/members/search"
        return route

    def _scoped_root(self, target: LocatorBundle) -> QueryRoot:
        root: QueryRoot = self.page
        for frame in target.scope.frame_path:
            candidate = frame.locator
            if candidate.strategy is not LocatorStrategy.TITLE or not candidate.value:
                raise SurfaceError(
                    "frame_locator_unsupported", "Frame scope must use a title locator."
                )
            root = root.frame_locator(f'iframe[title="{candidate.value}"]')
        return root

    @staticmethod
    def _locator(root: QueryRoot, candidate: LocatorCandidate) -> Locator:
        exact = candidate.match is MatchMode.EXACT
        if candidate.strategy is LocatorStrategy.ROLE_NAME:
            return cast(
                Locator,
                cast(Any, root).get_by_role(candidate.role, name=candidate.name, exact=exact),
            )
        if candidate.strategy is LocatorStrategy.LABEL:
            return root.get_by_label(candidate.value or "", exact=exact)
        if candidate.strategy is LocatorStrategy.TEXT:
            return root.get_by_text(candidate.value or "", exact=exact)
        if candidate.strategy is LocatorStrategy.PLACEHOLDER:
            return root.get_by_placeholder(candidate.value or "", exact=exact)
        if candidate.strategy is LocatorStrategy.TITLE:
            return root.get_by_title(candidate.value or "", exact=exact)
        if candidate.strategy is LocatorStrategy.CSS:
            return root.locator(candidate.value or "")
        if candidate.strategy is LocatorStrategy.RELATIVE_TEXT:
            anchor = root.get_by_text(candidate.anchor or "", exact=True)
            if candidate.relation == "form_submit":
                return cast(
                    Locator,
                    cast(Any, anchor.locator("xpath=ancestor::form")).get_by_role(
                        "button", name=candidate.text
                    ),
                )
            if candidate.relation == "following_value":
                return anchor.locator("xpath=following-sibling::*[1]")
        raise SurfaceError("locator_unsupported", "Locator strategy is not supported by web.v1.")

    def _target_locator(self, target: ResolvedTarget | None) -> Locator:
        if target is None:
            raise SurfaceError("target_required", "This action requires a resolved target.")
        try:
            return self._handles[target.handle]
        except KeyError as exc:
            raise SurfaceError(
                "target_handle_stale", "Resolved target is no longer available."
            ) from exc

    @staticmethod
    def _classify_target(locator: Locator) -> Risk | None:
        facts = locator.evaluate(
            """element => ({
                tag: element.tagName.toLowerCase(),
                type: (element.getAttribute('type') || '').toLowerCase(),
                formMethod: element.form ? element.form.method.toLowerCase() : null,
                href: element.closest('a') ? element.closest('a').getAttribute('href') : null
            })"""
        )
        if not isinstance(facts, dict):
            return None
        if facts.get("type") == "password":
            return Risk.SENSITIVE
        if facts.get("href") is not None or facts.get("formMethod") == "get":
            return Risk.READ_ONLY
        if facts.get("tag") in {"dd", "dt"}:
            return Risk.READ_ONLY
        return None

    @staticmethod
    def _required(locator: Locator | None) -> Locator:
        if locator is None:
            raise SurfaceError("target_required", "This action requires a resolved target.")
        return locator

    @staticmethod
    def _resolve_value(value: InputValue | LiteralValue, inputs: dict[str, Any]) -> Any:
        if isinstance(value, LiteralValue):
            return value.value
        try:
            return inputs[value.path]
        except KeyError as exc:
            raise SurfaceError(
                "input_binding_missing", "Action input binding is unavailable."
            ) from exc

    @staticmethod
    def _route_matches(route: str, pattern: str) -> bool:
        pieces = []
        for segment in pattern.strip("/").split("/"):
            pieces.append(
                "[^/]+" if segment == "*" or segment.startswith(":") else re.escape(segment)
            )
        return re.fullmatch(r"^/" + "/".join(pieces) + r"/?$", route) is not None
