"""
INTEGRATION: real HTTP over a real socket against a live local server.
The unit test for this same client mocks the network; here nothing is mocked.
"""

import pytest

pytestmark = pytest.mark.integration

from examples.api_client import fetch_json, get_user_name


def test_fetch_json_against_live_server(live_server):
    url = live_server({"/data": {"name": "Ada", "id": 1}})
    data = fetch_json(url + "/data")
    assert data == {"name": "Ada", "id": 1}


def test_get_user_name_end_to_end(live_server):
    url = live_server({"/user": {"name": "Grace"}})
    assert get_user_name(url + "/user") == "Grace"


def test_non_200_raises(live_server):
    url = live_server({"/user": {"name": "x"}})
    with pytest.raises(Exception):
        fetch_json(url + "/missing")  # 404 -> urllib raises, not silent
