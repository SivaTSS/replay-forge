from threading import Event
from unittest.mock import patch

import pytest

from replayforge.runs.viewing import ExecutionViewer, ViewingError, ViewingLimits, current_execution
from replayforge.surfaces.models import SurfaceFrame, Viewport


def png(value: bytes) -> SurfaceFrame:
    return SurfaceFrame(b"\x89PNG\r\n\x1a\n" + value, Viewport(800, 600))


def test_live_execution_authorization_history_and_detached_snapshots() -> None:
    ready, finish = Event(), Event()
    viewer = ExecutionViewer(ViewingLimits(maximum_frames=2, maximum_events=2))

    def operation() -> dict[str, str]:
        feed = current_execution.get()
        assert feed is not None
        feed.phase("replay")
        run = feed.bind("run_" + "1" * 32)
        for value in (b"one", b"two", b"three", b"three"):
            run.frame(png(value))
            run.event({"event_type": "step_verified"})
        ready.set()
        assert finish.wait(5)
        return {"status": "success"}

    try:
        started = viewer.start("replay", operation)
        assert ready.wait(5)
        key, token = started["execution_id"], started["viewer_token"]
        with pytest.raises(ViewingError, match="execution_unavailable"):
            viewer.snapshot(key, "wrong")
        with pytest.raises(ViewingError, match="execution_capacity_reached"):
            viewer.start("replay", operation)
        snapshot = viewer.snapshot(key, token)
        assert [frame["sequence"] for frame in snapshot["frames"]] == [2, 3]
        assert snapshot["evicted_frames"] == 1
        assert snapshot["first_event_sequence"] == 3
        assert viewer.frame(key, token, 2) == png(b"two").content
        with pytest.raises(ViewingError, match="frame_expired"):
            viewer.frame(key, token, 1)
        snapshot["frames"].clear()
        assert len(viewer.snapshot(key, token)["frames"]) == 2
        assert len(viewer.snapshot(key, token, after=3)["events"]) == 1
        finish.set()
        viewer._pool.shutdown(wait=True)
        assert viewer.snapshot(key, token)["state"] == "success"
        viewer.finish(key, {"status": "failure"})
        assert viewer.snapshot(key, token)["state"] == "success"
        with (
            patch("replayforge.runs.viewing.monotonic", return_value=10**15),
            pytest.raises(ViewingError, match="execution_unavailable"),
        ):
            viewer.snapshot(key, token)
    finally:
        finish.set()
        viewer.close()


def test_discovery_retains_latest_only_and_sanitizes_unhandled_failure() -> None:
    viewer = ExecutionViewer()

    def operation() -> dict[str, str]:
        feed = current_execution.get()
        assert feed is not None
        feed.phase("discovery")
        run = feed.bind("run_" + "2" * 32)
        run.frame(png(b"first"))
        run.frame(png(b"second"))
        run.frame(SurfaceFrame(b"not a PNG", Viewport(800, 600)))
        raise ValueError("private customer value")

    try:
        started = viewer.start("discovery", operation)
        viewer._pool.shutdown(wait=True)
        snapshot = viewer.snapshot(started["execution_id"], started["viewer_token"])
        assert len(snapshot["frames"]) == 1
        assert snapshot["result"] == {"status": "failure", "code": "execution_failed"}
        assert "private customer" not in str(snapshot)
    finally:
        viewer.close()


def test_pause_resume_keeps_identity_and_terminal_expiration() -> None:
    viewer = ExecutionViewer()
    captured = []

    def operation() -> dict[str, str]:
        feed = current_execution.get()
        assert feed is not None
        feed.phase("replay")
        captured.append(feed.bind("run_" + "3" * 32))
        return {"status": "intervention_required", "intervention_id": "int_" + "4" * 32}

    try:
        started = viewer.start("replay", operation)
        viewer._pool.shutdown(wait=True)
        key, token = started["execution_id"], started["viewer_token"]
        assert viewer.snapshot(key, token)["state"] == "paused"
        viewer.resume(captured[0])
        assert viewer.snapshot(key, token)["state"] == "running"
        captured[0].result({"status": "success"})
        assert viewer.snapshot(key, token)["state"] == "success"
    finally:
        viewer.close()


@pytest.mark.parametrize("limits", [{"maximum_active": 0}, {"maximum_active": 5}])
def test_invalid_limits(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ViewingLimits(**limits)


def test_byte_limit_and_terminal_capacity() -> None:
    viewer = ExecutionViewer(ViewingLimits(maximum_bytes=20, maximum_executions=1))
    ready = Event()
    done = Event()

    def operation() -> dict[str, str]:
        feed = current_execution.get()
        assert feed is not None
        run = feed.bind("run_" + "5" * 32)
        run.frame(png(b"a" * 30))
        run.frame(png(b"a" * 6))
        run.frame(png(b"b" * 6))
        ready.set()
        done.wait(5)
        return {"status": "unexpected"}

    try:
        started = viewer.start("replay", operation)
        assert ready.wait(5)
        key, token = started["execution_id"], started["viewer_token"]
        assert len(viewer.snapshot(key, token)["frames"]) == 1
        viewer.finish(key, {"status": "success"})
        second = viewer.start("discovery", lambda: {"status": "success"})
        with pytest.raises(ViewingError):
            viewer.snapshot(key, token)
        done.set()
        viewer._pool.shutdown(wait=True)
        assert viewer.snapshot(second["execution_id"], second["viewer_token"])["state"] == "success"
    finally:
        done.set()
        viewer.close()
    with pytest.raises(ViewingError, match="viewer_unavailable"):
        viewer.start("replay", operation)
