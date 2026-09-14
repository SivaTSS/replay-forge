"""Check the repository's Markdown conventions; not a general Markdown renderer."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

MERMAID_INIT = (
    '%%{init: {"htmlLabels":false,"themeVariables":{"lineColor":"#6E7781",'
    '"signalColor":"#6E7781"},"flowchart":{"curve":"linear"},'
    '"sequence":{"wrap":true}}}%%'
)
REPORT_HEADINGS = [
    "1. Architecture",
    "2. Artifact schema",
    "3. Determinism & error handling",
    "4. Heterogeneity & multi-tenant",
    "5. Escalation & handoff",
    "6. Safety",
    "7. Cuts",
]
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")
HEADING = re.compile(r"^(#{1,6}) (.+?)(?: +#+)?$")


def prose_and_diagrams(source: str) -> tuple[str, list[str], bool]:
    """Exclude fenced examples from link checks; retain Mermaid for style checks."""
    prose: list[str] = []
    diagrams: list[str] = []
    fence = ""
    language = ""
    block: list[str] = []
    for line in source.splitlines():
        if fence:
            if re.fullmatch(rf"\s*{re.escape(fence[0])}{{{len(fence)},}}\s*", line):
                if language == "mermaid":
                    diagrams.append("\n".join(block).strip())
                fence = ""
                block = []
            else:
                block.append(line)
            prose.append("")
        elif match := re.match(r"^\s*(`{3,}|~{3,})(.*)$", line):
            fence, language = match.groups()
            language = language.strip()
            prose.append("")
        else:
            prose.append(line)
    return "\n".join(prose), diagrams, not fence


def heading_anchors(prose: str) -> set[str]:
    """GitHub-style anchors for the ATX headings used by these documents."""
    anchors: set[str] = set()
    for line in prose.splitlines():
        if match := HEADING.fullmatch(line):
            base = re.sub(r"[^\w\- ]", "", match[2].lower()).replace(" ", "-")
            anchor = base
            suffix = 0
            while anchor in anchors:
                suffix += 1
                anchor = f"{base}-{suffix}"
            anchors.add(anchor)
    return anchors


def check_docs(root: Path) -> tuple[list[str], int, int]:
    paths = [root / "README.md", root / "REPORT.md", *sorted((root / "docs").glob("*.md"))]
    errors: list[str] = []
    diagram_count = 0
    for path in paths:
        name = path.relative_to(root)
        if not path.is_file():
            errors.append(f"{name}: missing document")
            continue
        prose, diagrams, closed = prose_and_diagrams(path.read_text())
        if not closed:
            errors.append(f"{name}: unclosed code fence")
        if sum(line.startswith("# ") for line in prose.splitlines()) != 1:
            errors.append(f"{name}: expected exactly one document title")
        lines = prose.splitlines()
        for index, line in enumerate(lines):
            if HEADING.fullmatch(line) and index + 1 < len(lines) and lines[index + 1]:
                errors.append(f"{name}:{index + 1}: missing blank line after heading")
        for target in LINK.findall(prose):
            url = urlsplit(target.strip("<>"))
            if url.scheme or url.netloc:
                continue
            destination = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not destination.exists():
                errors.append(f"{name}: missing link target {target}")
            elif url.fragment and destination.suffix == ".md":
                linked_prose, _, _ = prose_and_diagrams(destination.read_text())
                if unquote(url.fragment) not in heading_anchors(linked_prose):
                    errors.append(f"{name}: missing heading anchor {target}")
        for diagram in diagrams:
            diagram_count += 1
            if diagram.splitlines()[0] != MERMAID_INIT:
                errors.append(f"{name}: inconsistent Mermaid initialization")
            body = "\n".join(diagram.splitlines()[1:])
            if re.search(r"%%\{|<[^>]+>|^\s*(?:style|classDef|linkStyle)\b", body, re.M):
                errors.append(f"{name}: Mermaid overrides shared theme or uses HTML")
        if path.name == "REPORT.md":
            headings = [line[3:] for line in lines if line.startswith("## ")]
            if headings != REPORT_HEADINGS:
                errors.append(f"{name}: required seven-part report structure changed")
        if (
            path.parent == root / "docs"
            and path.name != "README.md"
            and "[Documentation index](README.md)" not in prose
        ):
            errors.append(f"{name}: missing documentation-index link")

    index_path = root / "docs/README.md"
    if not index_path.is_file():
        errors.append("docs/README.md: missing documentation index")
    else:
        index_prose, _, _ = prose_and_diagrams(index_path.read_text())
        targets = {urlsplit(link).path for link in LINK.findall(index_prose)}
        for path in paths[2:]:
            if path != index_path and path.name not in targets:
                errors.append(f"docs/README.md: missing index entry for {path.name}")
    return errors, len(paths), diagram_count


def main() -> int:
    errors, documents, diagrams = check_docs(Path(__file__).resolve().parents[1])
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Documentation checks passed: {documents} documents, {diagrams} Mermaid diagrams.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
