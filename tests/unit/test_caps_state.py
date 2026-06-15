from datetime import datetime, timedelta, timezone

import pytest
from caps.manifest import Capability
from caps.ledger import LedgerEntry
from caps.fingerprint import fingerprint
from caps.state import capability_state, BLOCK_STATES

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _cap(tmp_path, **kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="pytest", check_target="checks/test_x.py::test_x",
    )
    base.update(kw)
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    return Capability(**base)


@pytest.mark.unit
def test_never_proven_when_no_entry(tmp_path):
    assert capability_state(_cap(tmp_path), None, tmp_path, NOW) == "never-proven"


@pytest.mark.unit
def test_fail_and_error_passthrough(tmp_path):
    cap = _cap(tmp_path)
    fail = LedgerEntry(result="fail", at=NOW.isoformat(), tier="cheap")
    err = LedgerEntry(result="error", at=NOW.isoformat(), tier="cheap")
    assert capability_state(cap, fail, tmp_path, NOW) == "fail"
    assert capability_state(cap, err, tmp_path, NOW) == "error"


@pytest.mark.unit
def test_code_proven_vs_code_stale(tmp_path):
    cap = _cap(tmp_path, freshness="code")
    good = LedgerEntry(result="pass", at=NOW.isoformat(), tier="cheap",
                       fingerprint=fingerprint(cap, tmp_path))
    stale = LedgerEntry(result="pass", at=NOW.isoformat(), tier="cheap",
                        fingerprint="sha256:nope")
    assert capability_state(cap, good, tmp_path, NOW) == "proven"
    assert capability_state(cap, stale, tmp_path, NOW) == "code-stale"


@pytest.mark.unit
def test_time_proven_vs_time_expired(tmp_path):
    cap = _cap(tmp_path, tier="live", freshness="24h")
    recent = LedgerEntry(result="pass", at=(NOW - timedelta(hours=1)).isoformat(), tier="live")
    old = LedgerEntry(result="pass", at=(NOW - timedelta(hours=25)).isoformat(), tier="live")
    assert capability_state(cap, recent, tmp_path, NOW) == "proven"
    assert capability_state(cap, old, tmp_path, NOW) == "time-expired"


@pytest.mark.unit
def test_active_waiver_vs_expired(tmp_path):
    cap = _cap(tmp_path, tier="live", freshness="24h")
    active = LedgerEntry(result="waived", at=NOW.isoformat(), tier="live",
                         waiver={"reason": "x", "until": (NOW + timedelta(hours=2)).isoformat()})
    expired = LedgerEntry(result="waived", at=NOW.isoformat(), tier="live",
                          waiver={"reason": "x", "until": (NOW - timedelta(hours=2)).isoformat()})
    assert capability_state(cap, active, tmp_path, NOW) == "waived"
    assert capability_state(cap, expired, tmp_path, NOW) == "never-proven"


@pytest.mark.unit
def test_block_states_constant():
    assert BLOCK_STATES == {"never-proven", "fail", "error", "code-stale"}
