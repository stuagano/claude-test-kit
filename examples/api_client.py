"""
A tiny HTTP client — a realistic target for unit vs. integration testing.

  * Unit test    -> monkeypatch the network call; verify parsing/logic in isolation.
  * Integration  -> hit a REAL local HTTP server over a real socket.

Uses urllib (stdlib) so there's nothing to install.
"""

from __future__ import annotations

import json
import urllib.request


def fetch_json(url: str, *, timeout: float = 5.0) -> dict:
    """GET a URL and parse JSON. Raises on non-200 or invalid JSON (not silent)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"unexpected status {resp.status} for {url}")
        raw = resp.read().decode()
    return json.loads(raw)


def get_user_name(url: str) -> str:
    """Business logic on top of fetch_json — the part worth unit-testing in isolation."""
    data = fetch_json(url)
    if "name" not in data:
        raise KeyError(f"response from {url} missing 'name': {data}")
    return data["name"]
