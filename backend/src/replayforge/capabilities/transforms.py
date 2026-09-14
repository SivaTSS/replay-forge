"""Pure extraction semantics shared by discovery and deterministic replay."""


def transform_extracted_text(value: str, transform: str) -> str:
    if transform == "lowercase":
        return value.strip().lower()
    if transform == "decimal":
        return value.strip().removeprefix("$").replace(",", "")
    if transform in {"trim", "date-time"}:
        return value.strip()
    return value
