"""Shared domain bounds for discovery requests."""

from datetime import timedelta

MIN_DISCOVERY_STEPS = 1
MAX_DISCOVERY_STEPS = 50
DEFAULT_DISCOVERY_STEPS = 20
MIN_DISCOVERY_TIMEOUT = timedelta(seconds=10)
MAX_DISCOVERY_TIMEOUT = timedelta(minutes=10)
DEFAULT_DISCOVERY_TIMEOUT = timedelta(minutes=2)
