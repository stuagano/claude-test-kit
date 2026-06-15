"""
UNIT tests: isolate the unit, no real I/O.

Here we test the pure logic (normalize_key) and the business layer with the DB
mocked out. Fast, deterministic, runs on every save.
"""

import pytest

pytestmark = pytest.mark.unit

from examples.store import normalize_key


def test_normalize_key_trims_and_lowercases():
    assert normalize_key("  HeLLo  ") == "hello"


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_normalize_key_rejects_empty(bad):
    with pytest.raises((ValueError, TypeError)):
        normalize_key(bad)
