from datetime import datetime, timedelta, timezone

import pytest
from caps.ledger import LedgerEntry
from caps.freshness import parse_duration, waiver_active, FreshnessError


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_parse_duration():
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2d") == timedelta(days=2)
    with pytest.raises(FreshnessError):
        parse_duration("soon")


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
