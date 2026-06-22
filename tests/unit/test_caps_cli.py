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
def test_status_json_is_machine_readable(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: bad
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_bad.py::test_bad
    """)
    (p / "checks" / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    main(["verify"], cwd=str(p))                       # record a failure
    capsys.readouterr()                                # discard verify output
    rc = main(["status", "--json"], cwd=str(p))
    assert rc == 0
    doc = _json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["blocking"] == ["bad"]
    assert doc["summary"]["fail"] == 1
    cap = doc["capabilities"][0]
    assert cap["id"] == "bad" and cap["state"] == "fail"
    assert "test_bad" in cap["detail"]                 # failure evidence travels in the JSON


@pytest.mark.unit
def test_status_json_ok_true_when_all_proven(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: good
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["verify"], cwd=str(p))
    capsys.readouterr()                                # discard verify output
    main(["status", "--json"], cwd=str(p))
    doc = _json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["blocking"] == []


@pytest.mark.unit
def test_status_check_exits_nonzero_on_unproven(tmp_path, capsys):
    # The CI gate: an unproven capability must fail the build, and name what blocks.
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
    rc = main(["status", "--check"], cwd=str(tmp_path))
    err = capsys.readouterr().err
    assert rc == 1
    assert "writes-db" in err


@pytest.mark.unit
def test_status_check_exits_zero_when_all_proven(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: good
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["verify"], cwd=str(p))
    capsys.readouterr()                                # discard verify output
    rc = main(["status", "--check"], cwd=str(p))
    assert rc == 0


@pytest.mark.unit
def test_doctor_reports_missing_check_and_exits_nonzero(tmp_path, capsys):
    _project(tmp_path, """
        capabilities:
          - id: c1
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_absent.py::test_absent
    """)
    rc = main(["doctor", "--settings", str(tmp_path / "none.json")], cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "check missing" in out and "error(s)" in out


@pytest.mark.unit
def test_doctor_json_ok_on_clean_project(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: c1
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    rc = main(["doctor", "--json", "--settings", str(p / "none.json")], cwd=str(p))
    doc = _json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] is True


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
    entry = load_ledger(p / ".ctk" / "ledger.json")["bad-cap"]
    assert entry.result == "fail"
    # The failure output is persisted so the gate can show why without a re-run.
    assert entry.detail and "test_bad" in entry.detail


@pytest.mark.unit
def test_slowdown_note_fires_only_on_real_regression():
    from caps.cli import _slowdown_note
    # Doubled and grew by >= floor -> flagged.
    assert _slowdown_note("c", 1.0, 3.0) is not None
    # Minor jitter -> no note.
    assert _slowdown_note("c", 1.0, 1.2) is None
    # Doubled but absolute jump below the floor (sub-second) -> no note.
    assert _slowdown_note("c", 0.001, 0.3) is None
    # No prior timing -> nothing to compare.
    assert _slowdown_note("c", None, 5.0) is None
    # A recorded 0.0 is a real prior (sub-ms check, rounded), not "unknown" —
    # it must not silently disable detection.
    assert _slowdown_note("c", 0.0, 1.0) is not None


@pytest.mark.unit
def test_verify_records_duration(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: timed
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["verify"], cwd=str(p))
    from caps.ledger import load_ledger
    entry = load_ledger(p / ".ctk" / "ledger.json")["timed"]
    assert entry.duration is not None and entry.duration >= 0.0


@pytest.mark.unit
def test_status_json_includes_duration(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: timed
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    main(["verify"], cwd=str(p))
    capsys.readouterr()
    main(["status", "--json"], cwd=str(p))
    doc = _json.loads(capsys.readouterr().out)
    assert "duration" in doc["capabilities"][0]


@pytest.mark.unit
def test_verify_records_per_file_map_for_narrow_deps(tmp_path):
    p = _project(tmp_path, """
        capabilities:
          - id: cap
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: [ingest.py]
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (p / "ingest.py").write_text("x = 1\n")
    main(["verify"], cwd=str(p))
    from caps.ledger import load_ledger
    files = load_ledger(p / ".ctk" / "ledger.json")["cap"].files
    assert set(files) == {"checks/test_ok.py", "ingest.py"}


@pytest.mark.unit
def test_verify_skips_per_file_map_for_broad_glob(tmp_path):
    # A glob resolving to more than FILE_MAP_LIMIT files records no per-file map,
    # keeping the committed ledger lean.
    from caps.fingerprint import FILE_MAP_LIMIT
    p = _project(tmp_path, """
        capabilities:
          - id: cap
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: ["lib/**"]
            check: checks/test_ok.py::test_ok
    """)
    (p / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (p / "lib").mkdir()
    for i in range(FILE_MAP_LIMIT + 2):
        (p / "lib" / f"m{i}.py").write_text(f"v = {i}\n")
    main(["verify"], cwd=str(p))
    from caps.ledger import load_ledger
    assert load_ledger(p / ".ctk" / "ledger.json")["cap"].files is None
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
    main(["verify"], cwd=str(p))
    from caps.ledger import load_ledger
    assert load_ledger(p / ".ctk" / "ledger.json")["ok-cap"].detail is None


@pytest.mark.unit
def test_verify_stale_reproves_only_blocking_set(tmp_path):
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
    from caps.ledger import load_ledger
    # Prove 'a' so it is fresh; 'b' stays never-proven (i.e. stale to the gate).
    main(["verify", "--capability", "a"], cwd=str(p))
    a_at = load_ledger(p / ".ctk" / "ledger.json")["a"].at

    rc = main(["verify", "--stale"], cwd=str(p))
    assert rc == 0
    ledger = load_ledger(p / ".ctk" / "ledger.json")
    # 'b' got proven; 'a' was left untouched (its timestamp did not move).
    assert ledger["b"].result == "pass"
    assert ledger["a"].at == a_at


@pytest.mark.unit
def test_verify_stale_when_nothing_stale_is_a_noop(tmp_path, capsys):
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
    """)
    (p / "checks" / "test_a.py").write_text("def test_a():\n    assert True\n")
    main(["verify"], cwd=str(p))            # everything proven & fresh
    rc = main(["verify", "--stale"], cwd=str(p))
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing stale" in out.lower()


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
