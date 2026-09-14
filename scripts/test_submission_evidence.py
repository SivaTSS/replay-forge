"""Submission-gate regression tests. Synthetic records are not discovery evidence."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import capture_replay_evidence
import verify_evidence_bundles
from capture_replay_evidence import ReplayCase, ReplaySpecification, validate_result
from pydantic import ValidationError
from verify_evidence_bundles import submission_gaps

from replayforge.runs.results import FailureResult


class SubmissionEvidenceTests(unittest.TestCase):
    def test_empty_evidence_root_fails_instead_of_reporting_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("sys.argv", ["verify_evidence_bundles.py", temporary]),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                verify_evidence_bundles.main()
            self.assertEqual(raised.exception.code, 2)

    def test_existing_capture_destination_is_rejected_before_browser_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "replay-servicing-payoff").mkdir()
            with (
                patch(
                    "sys.argv",
                    [
                        "capture_replay_evidence.py",
                        "--commit-sha",
                        "abcdef1",
                        "--output-root",
                        str(root),
                    ],
                ),
                patch.object(capture_replay_evidence, "build_runtime") as build,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                capture_replay_evidence.main()
            self.assertEqual(raised.exception.code, 2)
            build.assert_not_called()

    def test_requires_both_modes_and_richer_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            examples: list[tuple[str, list[str], str, dict[str, dict[str, object]]]] = [
                (
                    "discovery",
                    ["discovery_started", "model_proposal_received", "artifact_compiled"],
                    "success",
                    {},
                ),
                ("replay", ["replay_started"], "success", {}),
                ("failure", ["replay_started"], "failure", {"screenshots/001.png": {}}),
            ]
            for name, events, status, attachments in examples:
                path = root / name
                path.mkdir()
                (path / "events.jsonl").write_text(
                    "\n".join(json.dumps({"event_type": event}) for event in events)
                )
                (path / "result.json").write_text(json.dumps({"status": status}))
                (path / "manifest.json").write_text(json.dumps({"attachments": attachments}))
                paths.append(path)
            self.assertEqual(len(submission_gaps([])), 3)
            self.assertEqual(
                submission_gaps(paths[:1]),
                {"successful model-free replay", "failed replay with richer evidence"},
            )
            self.assertEqual(submission_gaps(paths), set())
            (paths[2] / "manifest.json").write_text('{"attachments": {}}')
            self.assertEqual(submission_gaps(paths), {"failed replay with richer evidence"})
            (paths[1] / "events.jsonl").write_text(
                '{"event_type":"replay_started"}\n{"event_type":"model_proposal_received"}'
            )
            self.assertIn("successful model-free replay", submission_gaps(paths))

    def test_unsafe_case_names_and_unknown_fields_are_rejected(self) -> None:
        values: tuple[dict[str, object], ...] = (
            {"cases": {}},
            {"cases": {"../escape": {}}},
            {"cases": {}, "typo": True},
        )
        for value in values:
            with self.assertRaises(ValidationError):
                ReplaySpecification.model_validate(value)

    def test_mismatched_result_is_not_published_and_does_not_echo_values(self) -> None:
        case = ReplayCase.model_validate(
            {
                "capability_id": "member.example",
                "invocation": {"tenant": "harbor", "inputs": {}},
                "expected_status": "failure",
                "expected_code": "target_absent",
            }
        )
        result = FailureResult(
            status="failure",
            run_id="run_" + "a" * 32,
            code="target_absent",
            message="private value",
            recoverable=False,
            evidence_manifest="evidence://run_" + "a" * 32 + "/manifest.json",
        )
        validate_result(case, result)
        with self.assertRaisesRegex(ValueError, "failure code did not match"):
            validate_result(case, result.model_copy(update={"code": "permission_denied"}))
        with self.assertRaisesRegex(ValueError, "status did not match"):
            validate_result(case.model_copy(update={"expected_status": "success"}), result)
        with self.assertRaisesRegex(ValueError, "outputs did not match"):
            validate_result(
                case.model_copy(update={"expected_outputs": {"secret": "private"}}), result
            )


if __name__ == "__main__":
    unittest.main()
