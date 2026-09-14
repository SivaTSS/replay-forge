import pytest

from replayforge.capabilities.transforms import transform_extracted_text
from replayforge.discovery.engine import DiscoveryEngine
from replayforge.replay.engine import ReplayEngine


@pytest.mark.parametrize(
    ("transform", "value", "expected"),
    [
        ("text", " $9,035.70 ", " $9,035.70 "),
        ("trim", " $9,035.70 ", "$9,035.70"),
        ("trim", " Acme, Inc. ", "Acme, Inc."),
        ("decimal", " $9,035.70 ", "9035.70"),
        ("date-time", " September 21, 2026 ", "September 21, 2026"),
        ("lowercase", " STRAẞE ", "straße"),
    ],
)
def test_discovery_and_replay_share_exact_extraction_semantics(
    transform: str, value: str, expected: str
) -> None:
    assert transform_extracted_text(value, transform) == expected
    assert DiscoveryEngine._transform(value, transform) == expected
    assert ReplayEngine._transform(value, transform) == expected
