"""Bounded, ephemeral execution viewing. Never an evidence or browser-control store."""

from __future__ import annotations

import hashlib
import secrets
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any, Literal

from replayforge.shared.ids import EntityKind, new_id
from replayforge.surfaces.models import SurfaceFrame

Mode = Literal["replay", "discovery"]
State = Literal["running", "paused", "success", "failure", "business_outcome", "terminated"]


class ViewingError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class ViewingLimits:
    maximum_active: int = 1
    maximum_executions: int = 4
    maximum_frames: int = 60
    maximum_bytes: int = 32 * 1024 * 1024
    maximum_frame_bytes: int = 5 * 1024 * 1024
    maximum_events: int = 2000
    retention_seconds: int = 300

    def __post_init__(self) -> None:
        if any(getattr(self, name) < 1 for name in self.__dataclass_fields__):
            raise ValueError("viewing limits must be positive")
        if self.maximum_active > self.maximum_executions:
            raise ValueError("active execution limit exceeds retention capacity")


@dataclass(slots=True)
class _Execution:
    id: str
    mode: Mode
    token_hash: bytes
    state: State = "running"
    phase: str = "starting"
    frames: OrderedDict[int, tuple[dict[str, Any], bytes]] = field(default_factory=OrderedDict)
    events: list[dict[str, Any]] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    sequence: int = 0
    event_sequence: int = 0
    evicted_frames: int = 0
    byte_count: int = 0
    result: dict[str, Any] | None = None
    finished_at: float | None = None


@dataclass(frozen=True, slots=True)
class ExecutionFeed:
    manager: ExecutionViewer
    execution_id: str

    def phase(self, name: str) -> None:
        self.manager.phase(self.execution_id, name)

    def bind(self, run_id: str) -> RunFeed:
        return self.manager.bind(self.execution_id, run_id)


@dataclass(frozen=True, slots=True)
class RunFeed:
    manager: ExecutionViewer
    execution_id: str
    run_id: str
    phase: str

    def frame(self, frame: SurfaceFrame) -> None:
        self.manager.publish_frame(self, frame)

    def event(self, event: dict[str, Any]) -> None:
        self.manager.publish_event(self, event)

    def result(self, result: dict[str, Any]) -> None:
        self.manager.run_result(self, result)


# Read only at composition time, on the execution-launch thread. Callbacks capture
# the resulting RunFeed explicitly before crossing the browser's thread boundary.
current_execution: ContextVar[ExecutionFeed | None] = ContextVar("execution_view", default=None)


class ExecutionViewer:
    def __init__(self, limits: ViewingLimits | None = None) -> None:
        self.limits = limits or ViewingLimits()
        self._lock = RLock()
        self._items: OrderedDict[str, _Execution] = OrderedDict()
        self._pool = ThreadPoolExecutor(
            max_workers=self.limits.maximum_active, thread_name_prefix="execution-view"
        )
        self._closed = False
        self._stop = Event()
        self._reaper: Thread | None = None

    def start(self, mode: Mode, operation: Callable[[], dict[str, Any]]) -> dict[str, str]:
        with self._lock:
            self._expire()
            if self._closed:
                raise ViewingError("viewer_unavailable", 503)
            active = sum(item.finished_at is None for item in self._items.values())
            if active >= self.limits.maximum_active:
                raise ViewingError("execution_capacity_reached", 429)
            while len(self._items) >= self.limits.maximum_executions:
                oldest = next(key for key, item in self._items.items() if item.finished_at)
                del self._items[oldest]
            execution_id = str(new_id(EntityKind.EXECUTION))
            token = secrets.token_urlsafe(32)
            self._items[execution_id] = _Execution(
                execution_id, mode, hashlib.sha256(token.encode()).digest()
            )
            if self._reaper is None:
                self._reaper = Thread(target=self._reap, name="execution-expiry", daemon=True)
                self._reaper.start()
            self._pool.submit(self._execute, execution_id, operation)
            return {"execution_id": execution_id, "viewer_token": token}

    def _reap(self) -> None:
        while not self._stop.wait(1):
            with self._lock:
                self._expire()

    def _execute(self, execution_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        binding = current_execution.set(ExecutionFeed(self, execution_id))
        try:
            result = operation()
        except Exception:
            # Never expose provider exceptions, supplied inputs, or credential-bearing URLs.
            result = {"status": "failure", "code": "execution_failed"}
        finally:
            current_execution.reset(binding)
        self.finish(execution_id, result)

    def _expire(self) -> None:
        expired = [
            key
            for key, item in self._items.items()
            if item.finished_at is not None
            and monotonic() - item.finished_at >= self.limits.retention_seconds
        ]
        for key in expired:
            del self._items[key]

    def _authorize(self, execution_id: str, token: str) -> _Execution:
        self._expire()
        item = self._items.get(execution_id)
        if item is None or not secrets.compare_digest(
            item.token_hash, hashlib.sha256(token.encode()).digest()
        ):
            raise ViewingError("execution_unavailable", 404)
        return item

    def snapshot(self, execution_id: str, token: str, after: int = 0) -> dict[str, Any]:
        with self._lock:
            item = self._authorize(execution_id, token)
            return deepcopy(
                {
                    "execution_id": item.id,
                    "mode": item.mode,
                    "state": item.state,
                    "phase": item.phase,
                    "run_ids": item.run_ids,
                    "frames": [metadata for metadata, _ in item.frames.values()],
                    "evicted_frames": item.evicted_frames,
                    "events": [event for event in item.events if event["sequence"] > after],
                    "event_cursor": item.event_sequence,
                    "first_event_sequence": item.events[0]["sequence"] if item.events else None,
                    "result": item.result,
                    "retention_seconds": self.limits.retention_seconds,
                }
            )

    def frame(self, execution_id: str, token: str, sequence: int) -> bytes:
        with self._lock:
            item = self._authorize(execution_id, token)
            frame = item.frames.get(sequence)
            if frame is None:
                raise ViewingError("frame_expired", 410)
            return frame[1]

    def phase(self, execution_id: str, name: str) -> None:
        with self._lock:
            item = self._items.get(execution_id)
            if item is not None and item.finished_at is None:
                item.phase = name

    def bind(self, execution_id: str, run_id: str) -> RunFeed:
        with self._lock:
            item = self._items[execution_id]
            if run_id not in item.run_ids:
                item.run_ids.append(run_id)
            return RunFeed(self, execution_id, run_id, item.phase)

    def publish_frame(self, feed: RunFeed, frame: SurfaceFrame) -> None:
        data = frame.content
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > min(
            self.limits.maximum_frame_bytes, self.limits.maximum_bytes
        ):
            return
        with self._lock:
            item = self._items.get(feed.execution_id)
            if item is None or item.finished_at is not None:
                return
            if item.frames:
                last_metadata, last_data = next(reversed(item.frames.values()))
                if last_data == data and last_metadata["run_id"] == feed.run_id:
                    return
            item.sequence += 1
            item.frames[item.sequence] = (
                {
                    "sequence": item.sequence,
                    "run_id": feed.run_id,
                    "phase": feed.phase,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "width": frame.viewport.width,
                    "height": frame.viewport.height,
                    "event_sequence": item.event_sequence,
                },
                data,
            )
            item.byte_count += len(data)
            maximum = self.limits.maximum_frames if item.mode == "replay" else 1
            while len(item.frames) > maximum or item.byte_count > self.limits.maximum_bytes:
                _, (_, removed) = item.frames.popitem(last=False)
                item.byte_count -= len(removed)
                item.evicted_frames += 1

    def publish_event(self, feed: RunFeed, event: dict[str, Any]) -> None:
        with self._lock:
            item = self._items.get(feed.execution_id)
            if item is None or item.finished_at is not None:
                return
            item.event_sequence += 1
            item.events.append(
                {
                    **deepcopy(event),
                    "sequence": item.event_sequence,
                    "run_id": feed.run_id,
                    "phase": feed.phase,
                }
            )
            del item.events[: -self.limits.maximum_events]

    def run_result(self, feed: RunFeed, result: dict[str, Any]) -> None:
        if feed.phase == "replay":
            self.finish(feed.execution_id, result)

    def finish(self, execution_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            item = self._items.get(execution_id)
            if item is None or item.finished_at is not None:
                return
            status = result.get("status")
            if status == "intervention_required" and item.mode == "replay":
                item.state = "paused"
            elif status in {"success", "failure", "business_outcome", "terminated"}:
                item.state = status
                item.finished_at = monotonic()
            else:
                item.state = "failure"
                item.finished_at = monotonic()
                result = {"status": "failure", "code": "invalid_execution_result"}
            item.result = deepcopy(result)

    def resume(self, feed: RunFeed) -> None:
        with self._lock:
            item = self._items.get(feed.execution_id)
            if item is not None and item.finished_at is None:
                item.state = "running"
                item.result = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._stop.set()
        if self._reaper is not None:
            self._reaper.join()
        self._pool.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            self._items.clear()
