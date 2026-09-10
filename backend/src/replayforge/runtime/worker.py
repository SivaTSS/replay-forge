"""Bounded single-thread executor for thread-affine surface sessions."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Full, Queue
from threading import Lock, Thread
from typing import Generic, TypeVar, cast

T = TypeVar("T")
_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class _WorkItem(Generic[T]):
    operation: Callable[[], T]
    future: Future[T]


@dataclass(slots=True)
class SerialSessionWorker:
    """Run all operations for one external session on its owning thread."""

    name: str
    queue_capacity: int = 32
    _queue: Queue[object] = field(init=False)
    _thread: Thread = field(init=False)
    _lock: Lock = field(init=False, default_factory=Lock)
    _accepting: bool = field(init=False, default=True)

    def __post_init__(self) -> None:
        if self.queue_capacity < 1 or self.queue_capacity > 1_024:
            raise ValueError("worker queue capacity must be between 1 and 1024")
        self._queue = Queue(maxsize=self.queue_capacity)
        self._thread = Thread(
            target=self._run,
            name=f"replayforge-{self.name}",
            daemon=False,
        )
        self._thread.start()

    def call(self, operation: Callable[[], T]) -> T:
        future: Future[T] = Future()
        item = _WorkItem(operation, future)
        with self._lock:
            if not self._accepting:
                raise RuntimeError("session worker is closed")
            try:
                self._queue.put_nowait(item)
            except Full as error:
                raise RuntimeError("session worker queue is full") from error
        return future.result()

    def close(self, finalizer: Callable[[], object] | None = None) -> None:
        finalizer_future: Future[object] | None = None
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            if finalizer is not None:
                finalizer_future = Future()
                self._queue.put(_WorkItem(finalizer, finalizer_future))
            self._queue.put(_SENTINEL)
        self._thread.join()
        if finalizer_future is not None:
            finalizer_future.result()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                work = cast(_WorkItem[object], item)
                if not work.future.set_running_or_notify_cancel():
                    continue
                try:
                    work.future.set_result(work.operation())
                except BaseException as error:
                    work.future.set_exception(error)
            finally:
                self._queue.task_done()
