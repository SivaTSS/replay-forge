import pytest

from replayforge.shared.ids import EntityKind, new_id, parse_id


def test_new_id_round_trips_with_expected_kind() -> None:
    run_id = new_id(EntityKind.RUN)

    assert str(run_id).startswith("run_")
    assert parse_id(run_id, EntityKind.RUN) == run_id


def test_new_ids_are_unique() -> None:
    assert new_id(EntityKind.EVENT) != new_id(EntityKind.EVENT)


@pytest.mark.parametrize(
    "value",
    [
        "run_not-a-uuid",
        "unknown_0123456789abcdef0123456789abcdef",
        "run_0123456789abcdef0123456789abcdeg",
        "RUN_0123456789abcdef0123456789abcdef",
    ],
)
def test_parse_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        parse_id(value)


def test_parse_id_rejects_wrong_entity_kind() -> None:
    session_id = new_id(EntityKind.SESSION)

    with pytest.raises(ValueError, match="expected a run identifier"):
        parse_id(session_id, EntityKind.RUN)
