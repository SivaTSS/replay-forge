"""Opaque, typed identifiers used at domain and adapter boundaries."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

EntityId = NewType("EntityId", str)


class EntityKind(StrEnum):
    EXECUTION = "exe"
    RUN = "run"
    SUITE = "sui"
    SESSION = "ses"
    INTERVENTION = "int"
    EVIDENCE = "evd"
    DECISION = "dec"
    EVENT = "evt"
    TRACE = "trc"


_ID_PATTERN = re.compile(r"^(?P<prefix>[a-z]{3})_(?P<value>[0-9a-f]{32})$")


def new_id(kind: EntityKind) -> EntityId:
    """Create a non-sequential opaque identifier with a stable type prefix."""

    return EntityId(f"{kind.value}_{uuid4().hex}")


def parse_id(value: str, expected_kind: EntityKind | None = None) -> EntityId:
    """Validate an external identifier and optionally enforce its entity kind."""

    match = _ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("identifier must have a known three-letter prefix and UUID payload")

    try:
        kind = EntityKind(match.group("prefix"))
        UUID(hex=match.group("value"))
    except ValueError as exc:
        raise ValueError("identifier contains an unknown prefix or invalid UUID payload") from exc

    if expected_kind is not None and kind is not expected_kind:
        raise ValueError(f"expected a {expected_kind.value} identifier, received {kind.value}")
    return EntityId(value)
