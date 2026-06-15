from datetime import datetime, timedelta, timezone

import pytest
from caps.manifest import Capability
from caps.ledger import LedgerEntry
from caps.freshness import parse_duration, is_fresh, waiver_active, FreshnessError


def _cap(**kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="pytest", check_target="checks/x.py::t",
    )
    base.update(kw)
    return Capability(**base)


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_parse_duration():
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2d") == timedelta(days=2)
    with pytest.raises(FreshnessError):
        parse_duration("soon")


@pytest.mark.unit
def test_code_freshness_matches_fingerprint():
    cap = _cap(freshness="code")
    entry = LedgerEntry(result="pass", at=NOW.isoformat(), tier="cheap",
                        fingerprint="sha256:aaa")
    assert is_fresh(cap, entry, "sha256:aaa", NOW) is True
    assert is_fresh(cap, entry, "sha256:bbb", NOW) is False


@pytest.mark.unit
def test_time_freshness_expires():
    cap = _cap(tier="live", freshness="24h")
    recent = LedgerEntry(result="pass", at=(NOW - timedelta(hours=1)).isoformat(),
                         tier="live")
    stale = LedgerEntry(result="pass", at=(NOW - timedelta(hours=25)).isoformat(),
                        tier="live")
    assert is_fresh(cap, recent, "ignored", NOW) is True
    assert is_fresh(cap, stale, "ignored", NOW) is False


@pytest.mark.unit
def test_non_pass_is_never_fresh():
    cap = _cap(freshness="code")
    entry = LedgerEntry(result="fail", at=NOW.isoformat(), tier="cheap",
                        fingerprint="sha256:aaa")
    assert is_fresh(cap, entry, "sha256:aaa", NOW) is False
    assert is_fresh(cap, None, "sha256:aaa", NOW) is False


@pytest.mark.unit
def test_waiver_active_respects_until():
    active = LedgerEntry(result="waived", at=NOW.isoformat(), tier="live",
                         waiver={"reason": "offline",
                                 "until": (NOW + timedelta(hours=2)).isoformat()})
    expired = LedgerEntry(result="waived", at=NOW.isoformat(), tier="live",
                          waiver={"reason": "offline",
                                  "until": (NOW - timedelta(hours=2)).isoformat()})
    assert waiver_active(active, NOW) is True
    assert waiver_active(expired, NOW) is False
    assert waiver_active(None, NOW) is False
