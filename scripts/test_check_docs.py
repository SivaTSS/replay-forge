"""Regression tests for the lightweight documentation gate."""

import tempfile
import unittest
from pathlib import Path

from check_docs import (
    MERMAID_INIT,
    REPORT_HEADINGS,
    check_docs,
    heading_anchors,
    prose_and_diagrams,
)


class DocumentationChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text("# Project\n\n[Guide](docs/guide.md#an-example)\n")
        (self.root / "REPORT.md").write_text(
            "# Report\n\n" + "\n\n".join(f"## {heading}" for heading in REPORT_HEADINGS) + "\n"
        )
        (self.root / "docs/README.md").write_text("# Docs\n\n[Guide](guide.md)\n")
        self.guide = self.root / "docs/guide.md"
        self.guide.write_text(
            "# Guide\n\n[Documentation index](README.md)\n\n## An `example`\n\n"
            f"```mermaid\n{MERMAID_INIT}\nflowchart LR\n A --> B\n```\n"
        )

    def test_valid_docs(self) -> None:
        self.assertEqual(check_docs(self.root), ([], 4, 1))

    def test_missing_file_and_anchor(self) -> None:
        (self.root / "README.md").write_text(
            "# Project\n\n[Bad](absent.md) [Bad anchor](docs/guide.md#absent)\n"
        )
        errors, _, _ = check_docs(self.root)
        self.assertTrue(any("missing link target" in error for error in errors))
        self.assertTrue(any("missing heading anchor" in error for error in errors))

    def test_fences_exclude_examples_and_detect_unclosed_blocks(self) -> None:
        prose, diagrams, closed = prose_and_diagrams("~~~~text\n[Ignore](absent.md)\n~~~~\n")
        self.assertNotIn("Ignore", prose)
        self.assertEqual(diagrams, [])
        self.assertTrue(closed)
        self.assertFalse(prose_and_diagrams("```text\nunclosed")[2])

    def test_repeated_heading_anchors(self) -> None:
        self.assertEqual(heading_anchors("## A `test`\n## A test\n"), {"a-test", "a-test-1"})

    def test_diagram_theme_drift(self) -> None:
        self.guide.write_text(self.guide.read_text().replace(MERMAID_INIT, "%% custom theme"))
        errors, _, _ = check_docs(self.root)
        self.assertTrue(any("inconsistent Mermaid" in error for error in errors))

    def test_html_and_style_overrides(self) -> None:
        for directive in ("style A fill:purple", "A[Line<br/>break]"):
            with self.subTest(directive=directive):
                source = self.guide.read_text()
                self.guide.write_text(source.replace(" A --> B", directive))
                errors, _, _ = check_docs(self.root)
                self.assertTrue(any("overrides shared theme" in error for error in errors))
                self.guide.write_text(source)

    def test_report_and_navigation_drift(self) -> None:
        (self.root / "REPORT.md").write_text("# Report\n\n## Unplanned structure\n")
        (self.root / "docs/README.md").write_text("# Docs\n")
        self.guide.write_text("# Guide\nNo blank line or navigation\n")
        errors, _, _ = check_docs(self.root)
        for expected in (
            "seven-part",
            "missing index entry",
            "missing documentation-index",
            "blank",
        ):
            self.assertTrue(any(expected in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
