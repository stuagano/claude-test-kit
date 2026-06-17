import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest
from caps.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.unit
def test_verify_preserves_active_waiver(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: lc
            description: x
            given: g
            when: w
            then: t
            tier: live
            deps: []
            check: checks/test_missing.py::test_missing
    """)
    # The check file does not exist on purpose; a bare verify must NOT run it
    # because it is waived.
    main(["ack", "lc", "--reason", "offline"], cwd=str(p))
    rc = main(["verify"], cwd=str(p))
    assert rc == 0
    from caps.ledger import load_ledger
    entry = load_ledger(p / ".ctk" / "ledger.json")["lc"]
    assert entry.result == "waived"
    assert entry.waiver["reason"] == "offline"


@pytest.mark.unit
def test_verify_explicit_capability_overrides_waiver(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: lc
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["ack", "lc", "--reason", "x"], cwd=str(p))
    rc = main(["verify", "--capability", "lc"], cwd=str(p))
    assert rc == 0
    from caps.ledger import load_ledger
    assert load_ledger(p / ".ctk" / "ledger.json")["lc"].result == "pass"


@pytest.mark.unit
def test_bad_manifest_returns_2_not_traceback(tmp_path):
    _project(tmp_path, """
        capabilities:
          - id: c
            description: x
            given: g
            when: w
            then: t
            tier: bogus
            deps: []
            check: checks/x.py::t
    """)
    rc = main(["status"], cwd=str(tmp_path))
    assert rc == 2


@pytest.mark.unit
def test_bad_duration_returns_2(tmp_path):
    _project(tmp_path, """
        capabilities:
          - id: c
            description: x
            given: g
            when: w
            then: t
            tier: live
            deps: []
            check: checks/x.py::t
    """)
    rc = main(["ack", "c", "--reason", "x", "--for", "soon"], cwd=str(tmp_path))
    assert rc == 2


@pytest.mark.unit
def test_missing_deps_warning_is_displayed(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: c
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["verify"], cwd=str(p))
    err = capsys.readouterr().err
    assert "warning" in err.lower() and "deps" in err.lower()


import json as _json


@pytest.mark.unit
def test_gate_blocks_on_unproven(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: g1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    payload = _json.dumps({"cwd": str(p), "stop_hook_active": False})
    from caps.cli import cmd_gate
    from datetime import datetime, timezone
    rc = cmd_gate(payload, datetime(2026, 6, 15, tzinfo=timezone.utc))
    out = capsys.readouterr().out
    assert rc == 0
    decision = _json.loads(out)
    assert decision["decision"] == "block"
    assert "g1" in decision["reason"]


@pytest.mark.unit
def test_gate_malformed_input_fails_open(capsys):
    from caps.cli import cmd_gate
    from datetime import datetime, timezone
    rc = cmd_gate("not json", datetime(2026, 6, 15, tzinfo=timezone.utc))
    out = capsys.readouterr().out
    assert rc == 0
    payload = _json.loads(out)
    assert "additionalContext" in payload["hookSpecificOutput"]
    assert "caps gate failed" in payload["hookSpecificOutput"]["additionalContext"]


@pytest.mark.unit
def test_add_creates_never_proven_capability(tmp_path):
    rc = main([
        "add", "--id", "added1", "--tier", "cheap",
        "--description", "d", "--given", "g", "--when", "w", "--then", "t",
        "--check", "checks/test_added1.py::test_added1",
    ], cwd=str(tmp_path))
    assert rc == 0
    from caps.manifest import load_manifest
    caps = load_manifest(tmp_path / "capabilities.yaml")
    assert [c.id for c in caps] == ["added1"]
    assert (tmp_path / "checks" / "test_added1.py").exists()


@pytest.mark.unit
def test_add_duplicate_returns_2(tmp_path):
    args = ["add", "--id", "d", "--tier", "cheap", "--description", "d",
            "--given", "g", "--when", "w", "--then", "t", "--check", "c.py::t"]
    assert main(args, cwd=str(tmp_path)) == 0
    assert main(args, cwd=str(tmp_path)) == 2


@pytest.mark.unit
def test_init_force_onto_kit_errors_cleanly(capsys):
    # `init --force` aimed at the kit itself (target resolves to the kit) must be
    # refused with a clean error + exit 2 — not an uncaught ValueError traceback.
    rc = main(["init", "--force"], cwd=str(REPO_ROOT))
    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err
    assert "refusing to overwrite the source" in captured.err
