import ast
from pathlib import Path


def test_replay_package_has_no_model_or_provider_dependency() -> None:
    package = Path(__file__).resolve().parents[3] / "src" / "replayforge" / "replay"
    forbidden_fragments = ("model_provider", "openai", "discovery")
    violations: list[str] = []

    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(fragment in module for module in modules for fragment in forbidden_fragments):
                violations.append(f"{source.name}:{node.lineno}:{','.join(modules)}")

    assert violations == []
