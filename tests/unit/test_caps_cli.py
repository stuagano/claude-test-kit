import textwrap
from datetime import datetime, timezone

import pytest
from caps.cli import main


def _project(tmp_path, manifest_body: str):
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent(manifest_body))
    return tmp_path


@pytest.mark.unit
def test_status_on_unproven_capability(tmp_path, capsys):
    _project(tmp_path, """
        capabilities:
          - id: writes-db
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["status"], cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "writes-db" in out
    assert "never proven" in out.lower()


@pytest.mark.unit
def test_status_errors_when_no_manifest(tmp_path, capsys):
    rc = main(["status"], cwd=str(tmp_path))
    err = capsys.readouterr().err
    assert rc == 2
    assert "no capabilities.yaml" in err.lower()


@pytest.mark.unit
def test_verify_records_pass_and_exits_zero(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: ok-cap
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    rc = main(["verify"], cwd=str(p))
    assert rc == 0
    from caps.ledger import load_ledger
    entry = load_ledger(p / ".ctk" / "ledger.json")["ok-cap"]
    assert entry.result == "pass"
    assert entry.fingerprint  # code freshness recorded a fingerprint


@pytest.mark.unit
def test_verify_records_fail_and_exits_nonzero(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: bad-cap
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_bad.py::test_bad
    """)
    (p / "checks" / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    rc = main(["verify"], cwd=str(p))
    assert rc == 1
    from caps.ledger import load_ledger
    assert load_ledger(p / ".ctk" / "ledger.json")["bad-cap"].result == "fail"


@pytest.mark.unit
def test_verify_single_capability(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: a
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_a.py::test_a
          - id: b
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_b.py::test_b
    """)
    (p / "checks" / "test_a.py").write_text("def test_a():\n    assert True\n")
    (p / "checks" / "test_b.py").write_text("def test_b():\n    assert True\n")
    rc = main(["verify", "--capability", "a"], cwd=str(p))
    assert rc == 0
    from caps.ledger import load_ledger
    ledger = load_ledger(p / ".ctk" / "ledger.json")
    assert "a" in ledger and "b" not in ledger


@pytest.mark.unit
def test_ack_records_waiver(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: live-cap
            description: x
            given: g
            when: w
            then: t
            tier: live
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["ack", "live-cap", "--reason", "offline, no infra"], cwd=str(p))
    assert rc == 0
    from caps.ledger import load_ledger
    entry = load_ledger(p / ".ctk" / "ledger.json")["live-cap"]
    assert entry.result == "waived"
    assert entry.waiver["reason"] == "offline, no infra"
    assert entry.waiver["until"]  # an expiry timestamp was set


@pytest.mark.unit
def test_ack_unknown_capability_errors(tmp_path):
    _project(tmp_path, """
        capabilities:
          - id: real
            description: x
            given: g
            when: w
            then: t
            tier: live
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["ack", "ghost", "--reason", "x"], cwd=str(tmp_path))
    assert rc == 2
