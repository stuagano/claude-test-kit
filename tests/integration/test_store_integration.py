"""
INTEGRATION: real sqlite engine, real file on disk.

This is the read-after-write test pattern from the start of all this: write a
value through the real storage layer, then read it back through a fresh path and
assert it survived. No mocks — if the SQL, the commit, or the schema is wrong,
this fails.
"""

import pytest

pytestmark = pytest.mark.integration

from examples.store import Store


def test_read_after_write_round_trip(db_path):
    store = Store(db_path)
    try:
        store.set("Greeting", "hello")
        assert store.get("greeting") == "hello"  # also proves key normalization end-to-end
    finally:
        store.close()


def test_persists_across_connections(db_path):
    """Write with one connection, read with a brand-new one — proves real durability."""
    w = Store(db_path)
    w.set("k", "v1")
    w.close()

    r = Store(db_path)
    try:
        assert r.get("k") == "v1"
    finally:
        r.close()


def test_upsert_overwrites(db_path):
    store = Store(db_path)
    try:
        store.set("k", "first")
        store.set("k", "second")
        assert store.get("k") == "second"
    finally:
        store.close()


def test_missing_key_returns_none(db_path):
    store = Store(db_path)
    try:
        assert store.get("nope") is None
    finally:
        store.close()
