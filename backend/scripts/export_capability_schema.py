"""Export the versioned capability artifact JSON Schema deterministically."""

from __future__ import annotations

import json
from pathlib import Path

from replayforge.capabilities.serialization import artifact_json_schema


def main() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    destination = repository_root / "schemas" / "capability-artifact-v1.schema.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(artifact_json_schema(), indent=2, sort_keys=True) + "\n"
    destination.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
