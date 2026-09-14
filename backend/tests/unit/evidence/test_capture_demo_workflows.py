"""CLI export tests use a stub capture; no provider or live evidence is involved."""

import runpy
import stat
import sys
from pathlib import Path

import pytest

from replayforge.evidence.discovery_capture import SuiteCaptureRequest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/capture_demo_workflows.py"


@pytest.mark.parametrize("collect", [False, True])
def test_partial_scenarios_are_collection_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collect: bool,
) -> None:
    calls: list[SuiteCaptureRequest] = []

    def capture(
        base_url: str,
        timeout: int,
        output: Path,
        request: SuiteCaptureRequest,
        resume_suite: str | None,
    ) -> dict[str, str]:
        calls.append(request)
        return {"status": "collected"}

    monkeypatch.setattr("replayforge.evidence.discovery_capture.capture_suite", capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--spec",
            str(ROOT / "config/servicing-discovery.yaml"),
            "--workflow",
            "temporary_card_lock",
            "--primary-version",
            "1.0.1",
            "--scenario",
            "card_expired",
            "--output-directory",
            str(tmp_path / "exports"),
            *(["--collect-only"] if collect else []),
        ],
    )
    if collect:
        runpy.run_path(str(SCRIPT), run_name="__main__")
        assert len(calls) == 1 and not calls[0].publish
        assert [item.code for item in calls[0].scenarios] == ["card_expired"]
    else:
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert error.value.code == 2
        assert not calls and not (tmp_path / "exports").exists()


def test_repeated_capture_uses_distinct_private_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs: list[Path] = []

    def capture(
        base_url: str,
        timeout: int,
        output: Path,
        request: SuiteCaptureRequest,
        resume_suite: str | None,
    ) -> dict[str, str]:
        assert request.expected_capability_id == "member.temporary_card_lock"
        assert not output.exists()
        outputs.append(output)
        return {"version": "1.0.1"}

    monkeypatch.setattr("replayforge.evidence.discovery_capture.capture_suite", capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--spec",
            str(ROOT / "config/servicing-discovery.yaml"),
            "--workflow",
            "temporary_card_lock",
            "--output-directory",
            str(tmp_path / "exports"),
        ],
    )
    for _ in range(2):
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert len(set(outputs)) == 2
    for output in outputs:
        assert output.name == "temporary_card_lock.yaml"
        assert output.parent.parent == tmp_path / "exports"
        assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700


def test_unsafe_workflow_name_is_rejected_before_creating_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = tmp_path / "invalid.yaml"
    spec.write_text("workflows:\n  ../escape: {}\n", encoding="utf-8")
    output = tmp_path / "exports"
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--spec", str(spec), "--output-directory", str(output)],
    )

    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert error.value.code == 2
    assert not output.exists()
