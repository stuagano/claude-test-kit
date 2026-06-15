"""
UNIT tests for the HTTP client: the network is MOCKED, so no socket is touched.
This is the classic "mock the boundary, test the logic" pattern using the
built-in monkeypatch fixture (no pytest-mock needed).
"""

import pytest

pytestmark = pytest.mark.unit

from examples import api_client


def test_get_user_name_parses_mocked_response(monkeypatch):
    # Replace the network call with a stub.
    monkeypatch.setattr(api_client, "fetch_json", lambda url: {"name": "Ada"})
    assert api_client.get_user_name("http://anything") == "Ada"


def test_get_user_name_raises_on_missing_field(monkeypatch):
    monkeypatch.setattr(api_client, "fetch_json", lambda url: {"id": 1})
    with pytest.raises(KeyError):
        api_client.get_user_name("http://anything")
