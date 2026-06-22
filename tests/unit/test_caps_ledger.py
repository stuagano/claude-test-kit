import pytest
from caps.ledger import LedgerEntry, load_ledger, save_ledger


@pytest.mark.unit
def test_load_missing_ledger_returns_empty(tmp_path):
    assert load_ledger(tmp_path / ".ctk" / "ledger.json") == {}


@pytest.mark.unit
def test_round_trip(tmp_path):
    path = tmp_path / ".ctk" / "ledger.json"
    entries = {
        "writes-db": LedgerEntry(
            result="pass", at="2026-06-15T07:30:00+00:00",
            tier="live", fingerprint="sha256:abc", waiver=None,
        )
    }
    save_ledger(path, entries)
    assert path.exists()
    loaded = load_ledger(path)
    assert loaded == entries
    assert loaded["writes-db"].result == "pass"


@pytest.mark.unit
def test_save_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / ".ctk" / "ledger.json"
    save_ledger(path, {})
    assert path.exists()


@pytest.mark.unit
def test_save_is_atomic_leaves_no_temp(tmp_path):
    # An overwrite must replace cleanly via temp+rename, never leaving a partial
    # .tmp behind for a concurrent verify to trip over.
    path = tmp_path / ".ctk" / "ledger.json"
    save_ledger(path, {"a": LedgerEntry(result="pass", at="t", tier="cheap")})
    save_ledger(path, {"b": LedgerEntry(result="fail", at="t", tier="cheap")})
    assert list(load_ledger(path)) == ["b"]            # fully replaced, valid JSON
    leftovers = [p.name for p in path.parent.iterdir() if p.name != "ledger.json"]
    assert leftovers == []                             # no .ledger.*.tmp residue
