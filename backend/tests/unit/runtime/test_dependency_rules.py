import ast
from pathlib import Path


def test_domain_and_adapters_do_not_depend_on_runtime_composition() -> None:
    source_root = Path(__file__).resolve().parents[3] / "src" / "replayforge"
    excluded_packages = {"runtime"}
    violations: list[str] = []

    for source in source_root.glob("**/*.py"):
        relative = source.relative_to(source_root)
        if relative.parts[0] in excluded_packages or relative.name == "main.py":
            continue
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(
                module == "replayforge.runtime" or module.startswith("replayforge.runtime.")
                for module in modules
            ):
                violations.append(f"{relative}:{node.lineno}:{','.join(modules)}")

    assert violations == []
