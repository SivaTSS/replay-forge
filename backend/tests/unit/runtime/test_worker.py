from __future__ import annotations

from threading import get_ident

import pytest

from replayforge.runtime.worker import SerialSessionWorker


def test_worker_serializes_operations_on_one_owner_thread() -> None:
    worker = SerialSessionWorker("test")
    caller_thread = get_ident()
    try:
        first = worker.call(get_ident)
        second = worker.call(get_ident)
    finally:
        worker.close()

    assert first == second
    assert first != caller_thread


def test_worker_propagates_operation_failure_and_remains_usable() -> None:
    worker = SerialSessionWorker("test")
    try:
        with pytest.raises(ValueError, match="operation failed"):
            worker.call(lambda: (_ for _ in ()).throw(ValueError("operation failed")))
        assert worker.call(lambda: "healthy") == "healthy"
    finally:
        worker.close()


def test_close_runs_finalizer_on_owner_thread_and_is_idempotent() -> None:
    worker = SerialSessionWorker("test")
    owner_thread = worker.call(get_ident)
    finalized_on: list[int] = []

    worker.close(lambda: finalized_on.append(get_ident()))
    worker.close()

    assert finalized_on == [owner_thread]
    with pytest.raises(RuntimeError, match="closed"):
        worker.call(lambda: None)


@pytest.mark.parametrize("capacity", [0, 1_025])
def test_worker_rejects_unbounded_or_empty_queue(capacity: int) -> None:
    with pytest.raises(ValueError, match="capacity"):
        SerialSessionWorker("test", queue_capacity=capacity)
