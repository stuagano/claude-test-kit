"""
UNIT test of the error-log guard logic (the autouse fail_on_error_log fixture).
"""

import logging

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.allow_error_logs  # this test deliberately emits an ERROR to exercise capture
def test_guard_captures_error_logs():
    from ctk.logguard import CapturingHandler

    handler = CapturingHandler()
    log = logging.getLogger("demo")
    log.addHandler(handler)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            log.error("caught but logged at ERROR")  # the dangerous pattern
    finally:
        log.removeHandler(handler)

    assert handler.records
    assert handler.records[0].levelno >= logging.ERROR


@pytest.mark.allow_error_logs
def test_opt_out_marker_allows_error_logs():
    logging.getLogger("demo").error("expected and allowed")
