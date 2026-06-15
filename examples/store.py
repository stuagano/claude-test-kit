"""
A small sqlite-backed key/value store — a realistic target for BOTH unit and
integration tests.

  * Unit test    -> exercise the pure logic (key normalization) with no DB.
  * Integration  -> open a REAL sqlite file and do a read-after-write round trip.

sqlite3 is stdlib, so the "integration" here uses a real database engine and a
real file on disk without any external service to install.
"""

from __future__ import annotations

import sqlite3
from typing import Optional


def normalize_key(key: str) -> str:
    """Pure function (easy to unit test): trim + lowercase, reject empties."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty string")
    return key.strip().lower()


class Store:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    def set(self, key: str, value: str) -> None:
        k = normalize_key(key)
        self._conn.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, value),
        )
        self._conn.commit()

    def get(self, key: str) -> Optional[str]:
        k = normalize_key(key)
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (k,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()
