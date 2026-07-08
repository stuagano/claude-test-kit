# `caps evidence` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `caps evidence <id>` — a read-only CLI command that prints a capability's
manifest contract (`given`/`when`/`then`/`check`) plus its current ledger proof (state,
timestamp, and state-specific detail/waiver/changed-deps), so knowledge-capture
workflows (e.g. `compound-engineering`'s `/ce-compound`) can cite real proof in a doc
instead of re-deriving whether something works.

**Architecture:** One new `cmd_evidence` in `caps/cli.py`, built by composing the
existing `_capability_report` helper (used by `cmd_status`) with four extra manifest
fields evidence needs but status never prints. No changes to `manifest.py`, `ledger.py`,
`state.py`, or `gate.py` — this reads through their existing public surface only.

**Tech Stack:** Python 3, argparse, pytest (matches the rest of `caps/`).

## Global Constraints

- No new frontmatter field in any external doc schema — `compound-engineering`'s
  `references/schema.yaml` is a fixed contract this project doesn't own. Evidence is
  plain text for a doc's prose body, not YAML.
- No edits to `~/.claude/plugins/cache/compound-engineering-plugin/...` — it's a
  versioned, shared third-party install; changes there would be overwritten on the
  next plugin update and affect every project on the machine.
- `cmd_status`'s existing behavior (text and `--json`) must not change — build a
  separate `_evidence_report` that composes `_capability_report`, don't mutate it.
- Unknown capability id → `error: no capability with id <id!r>` on stderr, exit 2 —
  same contract `cmd_ack` already uses.
- `--json` output must be valid JSON that round-trips through `json.loads`.

---

### Task 1: `caps evidence <id>` command + tests

**Files:**
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_cli.py`

**Interfaces:**
- Consumes: `_capability_report(cap, entry, state, root) -> dict` (existing, cli.py:65),
  `capability_state(capability, entry, root, now) -> str` (existing, `caps/state.py`),
  `load_manifest(path) -> list[Capability]` (existing, `caps/manifest.py`),
  `load_ledger(path) -> dict[str, LedgerEntry]` (existing, `caps/ledger.py`).
  `Capability` fields used: `id`, `description`, `given`, `when`, `then`, `tier`,
  `check_target` (the pytest node or shell command string — `check_kind` is not
  surfaced separately since both are equally citable as "how this is proven").
- Produces: `cmd_evidence(root: Path, now: datetime, cap_id: str, as_json: bool) -> int`
  — called from `main()`, same shape as `cmd_ack`.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/unit/test_caps_cli.py` (the file already has
`import json as _json` further up, from the existing gate tests):

```python
def _field(label: str, value: str) -> str:
    # Mirrors caps/cli.py's _EVIDENCE_LABEL_WIDTH (13 = len("description:") + 1)
    # so these assertions can't drift from the implementation by a hand-typo'd space.
    return f"{label + ':':<13}{value}"


@pytest.mark.unit
def test_evidence_prints_proven_capability(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: writes-db
            description: the ingest job writes rows and they read back
            given: a reachable database
            when: the ingest job runs
            then: the written rows are readable back
            tier: cheap
            deps: []
            check: checks/test_writes_db.py::test_writes_db
    """)
    (p / "checks" / "test_writes_db.py").write_text(
        "def test_writes_db():\n    assert True\n"
    )
    main(["verify"], cwd=str(p))
    capsys.readouterr()  # discard verify output
    rc = main(["evidence", "writes-db"], cwd=str(p))
    out = capsys.readouterr().out
    assert rc == 0
    assert _field("id", "writes-db") in out
    assert _field("description", "the ingest job writes rows and they read back") in out
    assert _field("given", "a reachable database") in out
    assert _field("when", "the ingest job runs") in out
    assert _field("then", "the written rows are readable back") in out
    assert _field("tier", "cheap") in out
    assert _field("check", "checks/test_writes_db.py::test_writes_db") in out
    assert _field("state", "proven") in out
    assert "at:" in out
    assert "duration:" in out
    assert "detail:" not in out
    assert "waiver:" not in out


@pytest.mark.unit
def test_evidence_prints_waiver_reason(tmp_path, capsys):
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
    main(["ack", "live-cap", "--reason", "offline, no infra"], cwd=str(p))
    capsys.readouterr()
    rc = main(["evidence", "live-cap"], cwd=str(p))
    out = capsys.readouterr().out
    assert rc == 0
    assert _field("state", "waived") in out
    assert _field("waiver", "offline, no infra (until") in out


@pytest.mark.unit
def test_evidence_prints_failure_detail(tmp_path, capsys):
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
    main(["verify"], cwd=str(p))
    capsys.readouterr()
    rc = main(["evidence", "bad-cap"], cwd=str(p))
    out = capsys.readouterr().out
    assert rc == 0
    assert _field("state", "fail") in out
    assert "detail:" in out and "test_bad" in out


@pytest.mark.unit
def test_evidence_never_proven_has_no_at_or_duration(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: fresh-cap
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["evidence", "fresh-cap"], cwd=str(p))
    out = capsys.readouterr().out
    assert rc == 0
    assert _field("state", "never-proven") in out
    assert "at:" not in out
    assert "duration:" not in out


@pytest.mark.unit
def test_evidence_unknown_id_errors(tmp_path, capsys):
    _project(tmp_path, """
        capabilities:
          - id: real
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["evidence", "ghost"], cwd=str(tmp_path))
    err = capsys.readouterr().err
    assert rc == 2
    assert "no capability with id" in err
    assert "ghost" in err


@pytest.mark.unit
def test_evidence_json_round_trips(tmp_path, capsys):
    p = _project(tmp_path, """
        capabilities:
          - id: writes-db
            description: d
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
    rc = main(["evidence", "writes-db", "--json"], cwd=str(p))
    doc = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["id"] == "writes-db"
    assert doc["description"] == "d"
    assert doc["given"] == "g" and doc["when"] == "w" and doc["then"] == "t"
    assert doc["check"] == "checks/test_ok.py::test_ok"
    assert doc["state"] == "proven"
    assert "at" in doc and "duration" in doc
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py -k evidence -v`
Expected: FAIL — `main` raises `SystemExit` with an argparse error (`invalid choice:
'evidence'`), since the `evidence` subcommand doesn't exist yet.

- [ ] **Step 3: Implement `_evidence_report` and `cmd_evidence` in `caps/cli.py`**

Add right after the existing `_capability_report` function (after line 83, before
`def cmd_status`):

```python
_EVIDENCE_FIELD_ORDER = [
    "id", "description", "given", "when", "then", "tier", "check",
    "state", "at", "duration", "detail", "waiver", "changed",
]
_EVIDENCE_LABEL_WIDTH = 13  # len("description:") + 1, the longest label


def _evidence_report(cap, entry, state, root) -> dict:
    """The full citable contract for one capability: `_capability_report`'s
    proof fields plus the manifest fields evidence needs but status never
    printed (description/given/when/check)."""
    rep = _capability_report(cap, entry, state, root)
    rep["description"] = cap.description
    rep["given"] = cap.given
    rep["when"] = cap.when
    rep["check"] = cap.check_target
    return rep


def _format_evidence(rep: dict) -> str:
    lines = []
    for key in _EVIDENCE_FIELD_ORDER:
        if key not in rep:
            continue
        label = f"{key}:".ljust(_EVIDENCE_LABEL_WIDTH)
        if key == "waiver":
            w = rep["waiver"]
            lines.append(f"{label}{w['reason']} (until {w['until']})")
        elif key == "duration":
            lines.append(f"{label}{rep['duration']:.2f}s")
        elif key == "changed":
            lines.append(f"{label}{', '.join(rep['changed'])}")
        else:
            lines.append(f"{label}{rep[key]}")
    return "\n".join(lines)
```

Then add `cmd_evidence`, right after `cmd_ack` (after line 210, before `def cmd_gate`):

```python
def cmd_evidence(root: Path, now: datetime, cap_id: str, as_json: bool = False) -> int:
    caps = {c.id: c for c in load_manifest(root / MANIFEST_NAME)}
    if cap_id not in caps:
        print(f"error: no capability with id {cap_id!r}", file=sys.stderr)
        return 2
    cap = caps[cap_id]
    ledger = load_ledger(root / LEDGER_REL)
    entry = ledger.get(cap.id)
    state = capability_state(cap, entry, root, now)
    rep = _evidence_report(cap, entry, state, root)
    if as_json:
        print(json.dumps(rep, indent=2))
    else:
        print(_format_evidence(rep))
    return 0
```

`capability_state` is already imported at the top of `caps/cli.py`
(`from .state import capability_state, BLOCK_STATES`) — no import changes needed.

- [ ] **Step 4: Wire the `evidence` subcommand into `main()`**

In the subparser section of `main()`, right after the `a = sub.add_parser("ack", ...)`
block (after the `a.add_argument("--for", ...)` line), add:

```python
    ev = sub.add_parser("evidence", help="print a capability's contract + proof (for citing in docs)")
    ev.add_argument("capability", help="capability id")
    ev.add_argument("--json", action="store_true", help="emit machine-readable JSON")
```

In the dispatch `try` block at the bottom of `main()` (where `status`/`verify`/`ack`/
`doctor` are dispatched), add:

```python
        if args.command == "evidence":
            return cmd_evidence(root, now, args.capability, args.json)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py -k evidence -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full unit suite to check for regressions**

Run: `.venv/bin/python -m pytest -m unit -q`
Expected: all pass (no change to `cmd_status`'s behavior or output).

- [ ] **Step 7: Commit**

```bash
git add caps/cli.py tests/unit/test_caps_cli.py
git commit -m "feat(caps): add \`caps evidence <id>\` command

Prints a capability's manifest contract (given/when/then/check) plus
its current ledger proof (state, timestamp, detail/waiver/changed),
so a capability's proof can be cited in a doc instead of re-derived."
```

---

### Task 2: Dogfood capability + docs

**Files:**
- Modify: `capabilities.yaml`
- Modify: `SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1's `cmd_evidence` (via the CLI, indirectly — proven by pointing a
  manifest `check` at Task 1's own tests, the same pattern every other CLI-facing
  capability in this manifest already uses, e.g. `status-json-is-machine-readable` ->
  `tests/unit/test_caps_cli.py::test_status_json_is_machine_readable`).
- Produces: nothing further downstream — this is the last task.

- [ ] **Step 1: Add the dogfood capability**

In `capabilities.yaml`, add a new entry (alphabetical-ish placement doesn't matter —
match wherever entries are currently ordered; adding at the end of the list is fine):

```yaml
  - id: evidence-prints-ledger-fields
    description: caps evidence prints the manifest+ledger fields for a capability, matching the ledger record
    given: a capability with a recorded ledger entry
    when: caps evidence <id> runs
    then: every printed field (state, tier, check, at, and state-specific detail/waiver/changed) matches the ledger and manifest
    tier: cheap
    deps: [caps/cli.py]
    check: tests/unit/test_caps_cli.py::test_evidence_prints_proven_capability
```

- [ ] **Step 2: Add the SKILL.md guidance**

In `SKILL.md`, insert a new item `3.` directly after the existing item `2.` (the
`caps add` guidance) and before the `**Manifest shape:**` heading:

```markdown
3. **If you're documenting a solved problem that involves a proven capability**
   (e.g. via `/ce-compound` or any similar knowledge-capture workflow) — run
   `python -m caps evidence <id>` and fold the relevant fields into the doc's
   body instead of re-deriving whether it actually works from scratch. There's
   no frontmatter slot for this (a doc schema like `ce-compound`'s is a fixed
   contract this project doesn't own); it reads naturally as prose under a
   "Prevention" / "Why This Matters" / "Related" section, e.g.: *"Verified by
   `caps`: capability `writes-to-lakebase`, proven 2026-07-07 — see
   `checks/test_lakebase_write.py::test_write_readback`. Re-verify with
   `python -m caps verify --capability writes-to-lakebase`."*
```

- [ ] **Step 3: Add the README.md command table row**

In `README.md`, in the fenced command block under `## Commands`, add a line right
after the `ack` line:

```
python -m caps ack <id> --reason "..."   # time-boxed waiver when it genuinely can't be proven now
python -m caps evidence <id>              # print a capability's contract + proof, for citing in docs (e.g. ce-compound)
```

(The second line above is the new one — insert it immediately after the existing
`ack` line, keeping the existing `add`/`ponytail`/etc. lines that follow unchanged.)

- [ ] **Step 4: Prove the new capability**

Run: `PYTHONPATH=. .venv/bin/python -m caps verify --capability evidence-prints-ledger-fields`
Expected: `evidence-prints-ledger-fields: pass (...)`

- [ ] **Step 5: Confirm the Stop-hook gate is clean**

Run: `./bin/caps-stop-gate.sh; echo "exit: $?"`
Expected: `exit: 0` (no output before it — a clean gate prints nothing and exits 0)

- [ ] **Step 6: Commit**

```bash
git add capabilities.yaml SKILL.md README.md .ctk/ledger.json
git commit -m "feat(caps): document + dogfood \`caps evidence\`

Adds the evidence-prints-ledger-fields capability (proven via Task 1's
tests) and documents the citation convention in SKILL.md/README so any
project that vendors this kit picks it up automatically."
```

## Self-Review Notes

- **Spec coverage:** command + `--json` (Task 1), unknown-id error (Task 1), SKILL.md
  + README docs (Task 2), dogfood capability + test (Task 1 test referenced by Task 2's
  manifest entry). All spec sections covered.
- **No placeholders:** every step has literal code/commands, no TBD/TODO.
- **Type consistency:** `cmd_evidence(root: Path, now: datetime, cap_id: str, as_json: bool) -> int`
  matches the call site added in Step 4 of Task 1 (`cmd_evidence(root, now,
  args.capability, args.json)`), and matches the shape of the neighboring `cmd_ack`.
