import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from caps.gate import decide, GateDecision

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _project(tmp_path, body):
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent(body))
    return tmp_path


def _payload(tmp_path, **kw):
    p = {"cwd": str(tmp_path), "transcript_path": "", "stop_hook_active": False,
         "hook_event_name": "Stop"}
    p.update(kw)
    return p


CHEAP = """
capabilities:
  - id: c1
    description: d
    given: g
    when: w
    then: rows read back
    tier: cheap
    deps: []
    check: checks/test_x.py::test_x
"""


@pytest.mark.unit
def test_stop_hook_active_allows(tmp_path):
    _project(tmp_path, CHEAP)
    assert decide(_payload(tmp_path, stop_hook_active=True), NOW).block is False


@pytest.mark.unit
def test_no_manifest_allows(tmp_path):
    assert decide(_payload(tmp_path), NOW).block is False


@pytest.mark.unit
def test_never_proven_blocks(tmp_path):
    _project(tmp_path, CHEAP)
    d = decide(_payload(tmp_path), NOW)
    assert d.block is True
    assert "c1" in d.reason


@pytest.mark.unit
def test_block_reason_includes_recorded_failure_detail(tmp_path):
    # A previously-recorded failure detail must surface in the block message so
    # the failure can be fixed without re-running the check.
    _project(tmp_path, CHEAP)
    from caps.ledger import LedgerEntry, save_ledger
    from caps.project import LEDGER_REL
    save_ledger(tmp_path / LEDGER_REL, {"c1": LedgerEntry(
        result="fail", at=NOW.isoformat(), tier="cheap",
        detail="E   assert False\ntest_x.py:1: AssertionError")})
    d = decide(_payload(tmp_path), NOW)
    assert d.block is True
    assert "last failure" in d.reason
    assert "AssertionError" in d.reason
    assert "--stale" in d.reason  # single command to re-prove the blocking set


@pytest.mark.unit
def test_proven_fresh_allows(tmp_path):
    _project(tmp_path, CHEAP)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    from caps.manifest import load_manifest
    from caps.fingerprint import fingerprint
    from caps.ledger import LedgerEntry, save_ledger
    from caps.project import LEDGER_REL
    cap = load_manifest(tmp_path / "capabilities.yaml")[0]
    save_ledger(tmp_path / LEDGER_REL, {"c1": LedgerEntry(
        result="pass", at=NOW.isoformat(), tier="cheap",
        fingerprint=fingerprint(cap, tmp_path))})
    assert decide(_payload(tmp_path), NOW).block is False


@pytest.mark.unit
def test_code_stale_block_names_the_changed_dep(tmp_path):
    # Prove c1 against ingest.py, then change ingest.py: the cap goes code-stale
    # and the block message must name *which* dep drifted.
    _project(tmp_path, """
        capabilities:
          - id: c1
            description: d
            given: g
            when: w
            then: rows read back
            tier: cheap
            deps: [ingest.py]
            check: checks/test_x.py::test_x
    """)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "ingest.py").write_text("x = 1\n")
    from caps.manifest import load_manifest
    from caps.fingerprint import fingerprint, file_fingerprints
    from caps.ledger import LedgerEntry, save_ledger
    from caps.project import LEDGER_REL
    cap = load_manifest(tmp_path / "capabilities.yaml")[0]
    save_ledger(tmp_path / LEDGER_REL, {"c1": LedgerEntry(
        result="pass", at=NOW.isoformat(), tier="cheap",
        fingerprint=fingerprint(cap, tmp_path),
        files=file_fingerprints(cap, tmp_path))})
    (tmp_path / "ingest.py").write_text("x = 2\n")   # drift one dep
    d = decide(_payload(tmp_path), NOW)
    assert d.block is True
    assert "code-stale" in d.reason
    assert "changed since last proof" in d.reason
    assert "ingest.py" in d.reason


@pytest.mark.unit
def test_time_expired_does_not_block_but_notes(tmp_path):
    _project(tmp_path, """
        capabilities:
          - id: live1
            description: d
            given: g
            when: w
            then: app responds
            tier: live
            deps: []
            check: checks/test_x.py::test_x
    """)
    from caps.ledger import LedgerEntry, save_ledger
    from caps.project import LEDGER_REL
    save_ledger(tmp_path / LEDGER_REL, {"live1": LedgerEntry(
        result="pass", at=(NOW - timedelta(hours=30)).isoformat(), tier="live")})
    d = decide(_payload(tmp_path), NOW)
    assert d.block is False
    assert d.note and "live1" in d.note


@pytest.mark.unit
def test_resolves_via_transcript_path_when_cwd_blank(tmp_path):
    _project(tmp_path, CHEAP)
    fake_transcript = tmp_path / "sub" / "t.jsonl"
    fake_transcript.parent.mkdir()
    fake_transcript.write_text("")
    d = decide({"cwd": "", "transcript_path": str(fake_transcript),
                "stop_hook_active": False}, NOW)
    assert d.block is True
