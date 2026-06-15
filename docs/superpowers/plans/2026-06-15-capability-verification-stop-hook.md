# Capability Verification — Phase 2 (Stop-Hook Enforcement) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution note:** Task 1 (empirical probe) and Task 7 (global install + live test) run in the **main session with the user** — they trigger real Stop events / edit global settings and cannot be delegated to a subagent. Tasks 2–6 are pure code+TDD and are subagent-friendly.

**Goal:** A global `Stop` hook that blocks "done" while a project's declared capabilities are unproven/failed/code-stale, self-clearing via `stop_hook_active`, so capability verification is enforced in band rather than relying on the agent to remember.

**Architecture:** A read-only Python decision (`caps gate`) judges capability state from the ledger + fingerprints (no checks run on a turn boundary). A thin bash wrapper short-circuits cheaply when there's no `capabilities.yaml`, otherwise invokes `caps gate`. The shared `capability_state` function backs both `status` and the gate. Registered once in `~/.claude/settings.json`.

**Tech Stack:** Python 3 stdlib + the existing `caps`/`ctk` packages; bash wrapper; pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-15-capability-verification-stop-hook-design.md`

---

## File Structure

```
claude-test-kit/
├── caps/
│   ├── project.py     # NEW: MANIFEST_NAME, LEDGER_REL, find_root (extracted from cli)
│   ├── state.py       # NEW: capability_state(...) + BLOCK_STATES
│   ├── gate.py        # NEW: GateDecision, decide(payload, now), resolve_root
│   ├── cli.py         # MODIFY: import project.*; status uses capability_state; add `gate`, `install-hook`, `uninstall-hook`
│   └── hookinstall.py # NEW: idempotent settings.json install/uninstall
├── bin/
│   └── caps-stop-gate.sh   # NEW: wrapper (shell short-circuit -> caps gate)
├── tests/
│   ├── unit/
│   │   ├── test_caps_state.py
│   │   ├── test_caps_gate.py
│   │   └── test_caps_hookinstall.py
│   └── integration/
│       ├── test_caps_gate_cli.py     # `python -m caps gate` as a subprocess
│       └── test_caps_wrapper.py      # bin/caps-stop-gate.sh short-circuit + block
└── docs/superpowers/notes/stop-hook-probe-findings.md   # NEW (Task 1 output)
```

**Canonical names (keep identical across tasks):**
- States (strings): `proven`, `never-proven`, `fail`, `error`, `code-stale`, `time-expired`, `waived`.
- `BLOCK_STATES = {"never-proven", "fail", "error", "code-stale"}` (in `state.py`).
- `capability_state(cap, entry, root, now) -> str`.
- `GateDecision(block: bool, reason: Optional[str] = None, note: Optional[str] = None)`.
- `decide(payload: dict, now: datetime) -> GateDecision`.

Run tests with `.venv/bin/python -m pytest <path> -q`; full suite `./run_tests.sh`.

---

### Task 1: Empirically characterize the Stop-hook payload + `stop_hook_active` lifecycle

**Runs in the main session with the user — not a subagent.** Goal: confirm the real payload fields and, critically, *when `stop_hook_active` is true vs false*, before coding the gate.

**Files:**
- Create: `docs/superpowers/notes/stop-hook-probe-findings.md`

- [ ] **Step 1: Create the probe script**

```bash
cat > /tmp/caps_probe.sh <<'SH'
#!/bin/bash
input=$(cat)
printf '%s\n' "$input" >> /tmp/caps_probe.jsonl
# Block exactly once per cycle so we can watch stop_hook_active flip.
active=$(printf '%s' "$input" | sed -n 's/.*"stop_hook_active"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p')
if [ "$active" = "true" ]; then
  exit 0
fi
echo '{"decision":"block","reason":"caps probe: characterizing stop_hook_active (ignore)"}'
exit 0
SH
chmod +x /tmp/caps_probe.sh
: > /tmp/caps_probe.jsonl
```

- [ ] **Step 2: Register the probe (backup first), then have the user trigger turns**

Back up and add the hook to `~/.claude/settings.json` under `hooks.Stop` →
`{"hooks":[{"type":"command","command":"/tmp/caps_probe.sh","timeout":10}]}`.
Because Claude Code loads hooks at session start and treats hook changes as
security-sensitive, **the user may need to approve via `/hooks` or restart the
session** for the probe to fire. Confirm it's firing (lines appear in
`/tmp/caps_probe.jsonl`).

- [ ] **Step 3: Record findings**

Trigger: (a) a normal turn, (b) observe the block + the immediate re-stop, (c) a
fresh user message. Inspect `/tmp/caps_probe.jsonl` and write
`docs/superpowers/notes/stop-hook-probe-findings.md` answering:
- Exact payload fields present (vs the documented `session_id, transcript_path, cwd, permission_mode, hook_event_name, stop_hook_active`).
- Is `cwd` the project directory?
- `stop_hook_active`: value on a normal turn; on the immediate re-stop after a block; on a fresh user turn.
- Does blocking + self-clear (`stop_hook_active=true → exit 0`) behave as designed?

- [ ] **Step 4: Remove the probe, restore settings**

Remove the `hooks.Stop` probe entry from `~/.claude/settings.json` (restore from the backup if simpler). Confirm `/tmp/caps_probe.jsonl` stops growing.

- [ ] **Step 5: Reconcile**

If findings match the spec assumptions, proceed. If they differ (e.g., `cwd` is
not the project dir, or `stop_hook_active` never flips), note the required
adjustment to `gate.py`/wrapper in the findings file before Task 3/5. Commit the findings:

```bash
cd /Users/stuart.gano/Documents/claude-test-kit
git add docs/superpowers/notes/stop-hook-probe-findings.md
git commit -m "docs(caps): empirical Stop-hook payload + stop_hook_active findings"
```

---

### Task 2: `capability_state` (shared state logic) + refactor `status`

**Files:**
- Create: `caps/state.py`
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_state.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.state'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/state.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from .manifest import Capability
from .ledger import LedgerEntry
from .fingerprint import fingerprint
from .freshness import parse_duration, waiver_active

BLOCK_STATES = {"never-proven", "fail", "error", "code-stale"}


def capability_state(
    capability: Capability,
    entry: Optional[LedgerEntry],
    root: Union[str, Path],
    now: datetime,
) -> str:
    """One word for where a capability stands. Distinguishes code-stale
    (fingerprint changed -> the gate blocks) from time-expired (clock window
    passed -> the gate does NOT block, per the Phase 1 decision)."""
    if waiver_active(entry, now):
        return "waived"
    if entry is None:
        return "never-proven"
    if entry.result == "fail":
        return "fail"
    if entry.result == "error":
        return "error"
    if entry.result == "pass":
        if capability.freshness == "code":
            return "proven" if entry.fingerprint == fingerprint(capability, root) else "code-stale"
        window = parse_duration(capability.freshness)
        within = (now - datetime.fromisoformat(entry.at)) < window
        return "proven" if within else "time-expired"
    # result == "waived" but the waiver has expired (waiver_active was False),
    # or any unknown result: there is no live proof.
    return "never-proven"
```

- [ ] **Step 4: Refactor `cmd_status` to use `capability_state`**

In `caps/cli.py`, add to the imports:

```python
from .state import capability_state
```

Replace the existing `_status_label` function and the body of `cmd_status` with:

```python
_DISPLAY = {
    "proven": "proven",
    "never-proven": "never proven",
    "fail": "fail",
    "error": "error",
    "code-stale": "stale",
    "time-expired": "expired",
    "waived": "waived",
}
_GLYPH = {
    "proven": "OK ", "never proven": "----", "fail": "FAIL",
    "error": "ERR ", "stale": "STALE", "expired": "EXP ", "waived": "WAIV",
}


def cmd_status(root: Path, now: datetime) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    _print_warnings(caps)
    ledger = load_ledger(root / LEDGER_REL)
    for cap in caps:
        state = capability_state(cap, ledger.get(cap.id), root, now)
        label = _DISPLAY[state]
        print(f"[{_GLYPH.get(label, '?'):5}] {cap.id:30} {label}")
    return 0
```

(The old `_status_label` is now gone; `is_fresh`/`waiver_active`/`fingerprint`
imports remain used by other code, leave them.)

- [ ] **Step 5: Run tests to verify all pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_state.py tests/unit/test_caps_cli.py -q`
Expected: PASS — the new state tests and the *existing* cli tests (the unproven-status test still sees "never proven").

- [ ] **Step 6: Commit**

```bash
git add caps/state.py caps/cli.py tests/unit/test_caps_state.py
git commit -m "feat(caps): shared capability_state (code-stale vs time-expired); status uses it"
```

---

### Task 3: `project.py` (shared resolution) + `gate.py` (the decision)

**Files:**
- Create: `caps/project.py`
- Create: `caps/gate.py`
- Modify: `caps/cli.py` (import resolution helpers from `project`)
- Test: `tests/unit/test_caps_gate.py`

- [ ] **Step 1: Extract `project.py` and rewire `cli.py`**

Create `caps/project.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Optional

MANIFEST_NAME = "capabilities.yaml"
LEDGER_REL = Path(".ctk") / "ledger.json"


def find_root(start: Path) -> Optional[Path]:
    start = Path(start).resolve()
    for d in (start, *start.parents):
        if (d / MANIFEST_NAME).is_file():
            return d
    return None
```

In `caps/cli.py`: delete the local `MANIFEST_NAME`, `LEDGER_REL`, and `find_root`
definitions, and add to the imports:

```python
from .project import MANIFEST_NAME, LEDGER_REL, find_root
```

Verify nothing broke: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py -q` → all pass.

- [ ] **Step 2: Write the failing test for the gate**

Create `tests/unit/test_caps_gate.py`:

```python
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
    d = decide(_payload(tmp_path, stop_hook_active=True), NOW)
    assert d.block is False


@pytest.mark.unit
def test_no_manifest_allows(tmp_path):
    d = decide(_payload(tmp_path), NOW)   # tmp_path has no capabilities.yaml
    assert d.block is False


@pytest.mark.unit
def test_never_proven_blocks(tmp_path):
    _project(tmp_path, CHEAP)
    d = decide(_payload(tmp_path), NOW)
    assert d.block is True
    assert "c1" in d.reason


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
    d = decide(_payload(tmp_path), NOW)
    assert d.block is False


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
    assert d.block is False           # time-expiry never blocks
    assert d.note and "live1" in d.note


@pytest.mark.unit
def test_resolves_via_transcript_path_when_cwd_blank(tmp_path):
    _project(tmp_path, CHEAP)
    fake_transcript = tmp_path / "sub" / "t.jsonl"
    fake_transcript.parent.mkdir()
    fake_transcript.write_text("")
    d = decide({"cwd": "", "transcript_path": str(fake_transcript),
                "stop_hook_active": False}, NOW)
    assert d.block is True            # found the project via transcript path
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.gate'`.

- [ ] **Step 4: Write minimal implementation**

Create `caps/gate.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .project import MANIFEST_NAME, LEDGER_REL, find_root
from .manifest import load_manifest
from .ledger import load_ledger
from .state import capability_state, BLOCK_STATES


@dataclass
class GateDecision:
    block: bool
    reason: Optional[str] = None   # shown to Claude when block is True
    note: Optional[str] = None     # non-blocking additionalContext


def resolve_root(payload: dict) -> Optional[Path]:
    cwd = payload.get("cwd")
    if cwd:
        r = find_root(Path(cwd))
        if r:
            return r
    tp = payload.get("transcript_path")
    if tp:
        r = find_root(Path(tp).parent)
        if r:
            return r
    return None


def decide(payload: dict, now: datetime) -> GateDecision:
    if payload.get("stop_hook_active"):
        return GateDecision(block=False)

    root = resolve_root(payload)
    if root is None:
        return GateDecision(block=False)

    caps = load_manifest(root / MANIFEST_NAME)
    ledger = load_ledger(root / LEDGER_REL)

    blocking: list[tuple] = []   # (cap, state)
    expired: list = []           # cap
    for cap in caps:
        state = capability_state(cap, ledger.get(cap.id), root, now)
        if state in BLOCK_STATES:
            blocking.append((cap, state))
        elif state == "time-expired":
            expired.append(cap)

    note = None
    if expired:
        ids = ", ".join(c.id for c in expired)
        note = f"live capability time-expired (re-verify when convenient): {ids}"

    if not blocking:
        return GateDecision(block=False, note=note)

    lines = ["✗ Capabilities not proven & fresh — resolve before finishing:"]
    for cap, state in blocking:
        lines.append(f"  • {cap.id} [{state}]: {cap.then}")
        lines.append(f"    → python -m caps verify --capability {cap.id}")
    if note:
        lines.append(f"  (note) {note}")
    lines.append("Full status: python -m caps status   ·   "
                 "can't prove now? python -m caps ack <id> --reason \"...\"")
    return GateDecision(block=True, reason="\n".join(lines), note=note)
```

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_gate.py tests/unit/test_caps_cli.py -q`
Expected: PASS (gate tests + unchanged cli tests).

- [ ] **Step 6: Commit**

```bash
git add caps/project.py caps/gate.py caps/cli.py tests/unit/test_caps_gate.py
git commit -m "feat(caps): gate decision (read-only, blocks on BLOCK_STATES, time-expiry is a note)"
```

---

### Task 4: `caps gate` subcommand (stdin → decision)

**Files:**
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_cli.py` (append), `tests/integration/test_caps_gate_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_caps_cli.py`:

```python
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
```

Create `tests/integration/test_caps_gate_cli.py`:

```python
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]


def _gate(payload: dict, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run([sys.executable, "-m", "caps", "gate"],
                   cwd=str(cwd), env=env, input=json.dumps(payload))


@pytest.mark.integration
def test_gate_subprocess_blocks_then_clean(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent("""
        capabilities:
          - id: e1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """))
    (tmp_path / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    # Unproven -> gate blocks.
    r1 = _gate({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r1.returncode == 0
    assert json.loads(r1.stdout)["decision"] == "block"

    # Prove it, then gate is silent.
    env = dict(os.environ); env["PYTHONPATH"] = str(REPO_ROOT)
    ctk.run([sys.executable, "-m", "caps", "verify"], cwd=str(tmp_path), env=env).ok()
    r2 = _gate({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r2.returncode == 0
    assert r2.stdout.strip() == ""    # allow, no note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py::test_gate_blocks_on_unproven tests/integration/test_caps_gate_cli.py -q`
Expected: FAIL — `cmd_gate` / the `gate` subcommand don't exist yet.

- [ ] **Step 3: Write minimal implementation**

In `caps/cli.py`, add imports:

```python
import json
import sys
from .gate import decide
```

Add this function above `main`:

```python
def cmd_gate(stdin_text: str, now: datetime) -> int:
    try:
        payload = json.loads(stdin_text or "{}")
        decision = decide(payload, now)
    except Exception as e:  # fail open, but visibly
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": f"caps gate failed: {e} — capability enforcement skipped this turn",
        }}))
        return 0
    if decision.block:
        print(json.dumps({"decision": "block", "reason": decision.reason}))
    elif decision.note:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": decision.note}}))
    return 0
```

Register the subparser (alongside `status`/`verify`/`ack`):

```python
    sub.add_parser("gate", help="Stop-hook gate: read hook JSON on stdin, emit allow/block")
```

Now make `main` dispatch `gate` **before** the `find_root` precheck (the gate
resolves its own root from the payload and must not error when cwd has no
manifest). Replace the tail of `main` — from `args = parser.parse_args(argv)` to
the end — with:

```python
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)

    if args.command == "gate":
        return cmd_gate(sys.stdin.read(), now)

    start = Path(cwd) if cwd else Path.cwd()
    root = find_root(start)
    if root is None:
        print(f"error: no {MANIFEST_NAME} found from {start}", file=sys.stderr)
        return 2

    try:
        if args.command == "status":
            return cmd_status(root, now)
        if args.command == "verify":
            return cmd_verify(root, now, args.only)
        if args.command == "ack":
            return cmd_ack(root, now, args.capability, args.reason, args.for_)
    except (ManifestError, FreshnessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py tests/integration/test_caps_gate_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add caps/cli.py tests/unit/test_caps_cli.py tests/integration/test_caps_gate_cli.py
git commit -m "feat(caps): `caps gate` subcommand (stdin -> block/allow JSON, fail-open)"
```

---

### Task 5: The wrapper script (`bin/caps-stop-gate.sh`)

**Files:**
- Create: `bin/caps-stop-gate.sh`
- Test: `tests/integration/test_caps_wrapper.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_caps_wrapper.py`:

```python
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "caps-stop-gate.sh"


def _run(payload: dict, cwd, python=None):
    env = dict(os.environ)
    env["CAPS_KIT"] = str(REPO_ROOT)
    if python is not None:
        env["CAPS_GATE_PYTHON"] = python
    else:
        env["CAPS_GATE_PYTHON"] = sys.executable
        env["PYTHONPATH"] = str(REPO_ROOT)
    return ctk.run(["bash", str(WRAPPER)], cwd=str(cwd), env=env,
                   input=json.dumps(payload))


@pytest.mark.integration
def test_short_circuits_without_manifest(tmp_path):
    # Point the python at /bin/false: if the wrapper launched it, we'd see exit!=0
    # or output. No manifest -> it must exit 0 before launching python.
    r = _run({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path,
             python="/bin/false")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@pytest.mark.integration
def test_blocks_with_stale_manifest(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent("""
        capabilities:
          - id: w1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """))
    r = _run({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout)["decision"] == "block"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_caps_wrapper.py -q`
Expected: FAIL — the wrapper file does not exist.

- [ ] **Step 3: Write the wrapper**

Create `bin/caps-stop-gate.sh`:

```bash
#!/bin/bash
# Stop-hook gate wrapper for caps. Reads the hook JSON on stdin. Cheaply
# short-circuits (no Python) when the project has no capabilities.yaml, so the
# vast majority of turns cost only a bash walk-up. Fails OPEN (exit 0) on any
# missing dependency so a broken gate never bricks the ability to finish a turn.
set -u
input=$(cat)

# Resolve the project cwd from the payload; fall back to the process cwd.
cwd=$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
[ -z "$cwd" ] && cwd="$PWD"

# Walk up looking for capabilities.yaml.
found=""
dir="$cwd"
while :; do
  if [ -f "$dir/capabilities.yaml" ]; then found="$dir"; break; fi
  parent=$(dirname "$dir")
  [ "$parent" = "$dir" ] && break
  dir="$parent"
done
[ -z "$found" ] && exit 0   # no manifest -> nothing to enforce

KIT="${CAPS_KIT:-/Users/stuart.gano/Documents/claude-test-kit}"
PYTHON="${CAPS_GATE_PYTHON:-$KIT/.venv/bin/python}"
[ -x "$PYTHON" ] || { command -v "$PYTHON" >/dev/null 2>&1 || exit 0; }  # venv missing -> fail open

printf '%s' "$input" | PYTHONPATH="${PYTHONPATH:-$KIT}" "$PYTHON" -m caps gate
exit 0
```

Make it executable:

```bash
chmod +x /Users/stuart.gano/Documents/claude-test-kit/bin/caps-stop-gate.sh
```

- [ ] **Step 4: Run test to verify pass**

Run: `.venv/bin/python -m pytest tests/integration/test_caps_wrapper.py -q`
Expected: PASS (short-circuit exits 0 with no output; stale manifest blocks).

- [ ] **Step 5: Commit**

```bash
git add bin/caps-stop-gate.sh tests/integration/test_caps_wrapper.py
git commit -m "feat(caps): stop-gate wrapper with shell short-circuit + fail-open"
```

---

### Task 6: `caps install-hook` / `uninstall-hook`

**Files:**
- Create: `caps/hookinstall.py`
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_hookinstall.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_hookinstall.py`:

```python
import json
from pathlib import Path

import pytest
from caps.hookinstall import install_hook, uninstall_hook, HOOK_TAG


@pytest.mark.unit
def test_install_into_empty_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    data = json.loads(settings.read_text())
    stops = data["hooks"]["Stop"]
    assert any(h.get("hooks", [{}])[0].get("command") == "/x/caps-stop-gate.sh"
               for h in stops)
    # Backup created.
    assert list(tmp_path.glob("settings.json.bak.*"))


@pytest.mark.unit
def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    stops = json.loads(settings.read_text())["hooks"]["Stop"]
    ours = [h for h in stops if h.get("_caps") == HOOK_TAG]
    assert len(ours) == 1


@pytest.mark.unit
def test_install_preserves_existing_hooks_and_keys(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "env": {"A": "1"},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]},
    }))
    install_hook(settings, command="/x/caps-stop-gate.sh")
    data = json.loads(settings.read_text())
    assert data["env"] == {"A": "1"}
    cmds = [h["hooks"][0]["command"] for h in data["hooks"]["Stop"]]
    assert "/other.sh" in cmds and "/x/caps-stop-gate.sh" in cmds


@pytest.mark.unit
def test_uninstall_removes_only_ours(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]},
    }))
    install_hook(settings, command="/x/caps-stop-gate.sh")
    uninstall_hook(settings)
    cmds = [h["hooks"][0]["command"] for h in json.loads(settings.read_text())["hooks"]["Stop"]]
    assert cmds == ["/other.sh"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_hookinstall.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.hookinstall'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/hookinstall.py`:

```python
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

HOOK_TAG = "caps-stop-gate"


def _backup(path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = path.with_suffix(path.suffix + f".bak.{stamp}")
    candidate = base
    n = 2
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak.{stamp}-{n}")
        n += 1
    shutil.copy2(path, candidate)


def _entry(command: str) -> dict:
    return {"_caps": HOOK_TAG,
            "hooks": [{"type": "command", "command": command, "timeout": 10}]}


def install_hook(settings_path: Union[str, Path], command: str) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}")
    if settings_path.exists():
        _backup(settings_path)
    hooks = data.setdefault("hooks", {})
    stops = hooks.setdefault("Stop", [])
    stops[:] = [h for h in stops if h.get("_caps") != HOOK_TAG]   # idempotent
    stops.append(_entry(command))
    settings_path.write_text(json.dumps(data, indent=2) + "\n")


def uninstall_hook(settings_path: Union[str, Path]) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}")
    if settings_path.exists():
        _backup(settings_path)
    stops = data.get("hooks", {}).get("Stop", [])
    data.setdefault("hooks", {})["Stop"] = [h for h in stops if h.get("_caps") != HOOK_TAG]
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
```

- [ ] **Step 4: Wire CLI subcommands**

In `caps/cli.py`, add import:

```python
from .hookinstall import install_hook, uninstall_hook
```

Register the subparsers near the others. Note the `--command` option uses
`dest="hook_command"` so it does NOT collide with the subparser's own `command`
dest (which holds the chosen subcommand name):

```python
    ih = sub.add_parser("install-hook", help="register the Stop-hook gate in settings.json")
    ih.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ih.add_argument("--command", dest="hook_command", default=None,
                    help="hook command (defaults to this kit's bin/caps-stop-gate.sh)")
    uh = sub.add_parser("uninstall-hook", help="remove the Stop-hook gate from settings.json")
    uh.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
```

These two commands don't need a project root. In `main`, handle them next to
`gate` (before the `find_root` precheck):

```python
    if args.command == "gate":
        return cmd_gate(sys.stdin.read(), now)
    if args.command == "install-hook":
        kit = Path(__file__).resolve().parent.parent
        cmd = args.hook_command or str(kit / "bin" / "caps-stop-gate.sh")
        venv_py = kit / ".venv" / "bin" / "python"
        if not venv_py.exists():
            print(f"warning: {venv_py} not found — run ./run_tests.sh once so the "
                  f"hook has an interpreter (gate will fail open until then)", file=sys.stderr)
        install_hook(args.settings, command=cmd)
        print(f"installed Stop-hook gate -> {args.settings}")
        return 0
    if args.command == "uninstall-hook":
        uninstall_hook(args.settings)
        print(f"removed Stop-hook gate from {args.settings}")
        return 0
```

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_hookinstall.py tests/unit/test_caps_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add caps/hookinstall.py caps/cli.py tests/unit/test_caps_hookinstall.py
git commit -m "feat(caps): install-hook/uninstall-hook (idempotent, backup, venv check)"
```

---

### Task 7: Full suite, global install, live verification (main session + user)

**Not a subagent task.** Final wiring and an end-to-end live check.

- [ ] **Step 1: Full suite green**

Run: `./run_tests.sh`
Expected: all tests PASS (Phase 1 + Phase 2).

- [ ] **Step 2: Install the hook globally**

Run: `.venv/bin/python -m caps install-hook`
Then confirm with the user via `/hooks` that the `Stop` hook is registered (and
approve it / restart if Claude Code requires it for the new hook to take effect).

- [ ] **Step 3: Live block test**

In a scratch project with a `capabilities.yaml` that has one never-proven cheap
capability, end a turn and confirm the gate blocks with the reason; run
`python -m caps verify`; confirm the next turn is allowed. In a directory with no
`capabilities.yaml`, confirm turns finish normally (no interference).

- [ ] **Step 4: Confirm the self-clear**

Confirm that when blocked, the immediate re-stop is allowed (no infinite loop),
consistent with the Task 1 `stop_hook_active` findings.

- [ ] **Step 5: Commit any doc updates**

If Task 1 findings required gate/wrapper adjustments, ensure they're committed.
Update the SKILL.md note that previously said enforcement was "planned Phase 2"
to reflect that the hook now exists.

```bash
git add -A && git commit -m "docs(caps): Phase 2 stop-hook enforcement is live"
```

---

## Self-Review

**Spec coverage:**
- Read-only gate, blocks on never-proven/fail/error/code-stale, time-expiry is a note → Task 3 ✓
- `capability_state` distinguishes code-stale vs time-expired → Task 2 ✓
- Self-clearing via `stop_hook_active` → Task 3 (`decide`) ✓, verified Tasks 1 & 7
- `caps gate` stdin→decision, fail-open-but-visible → Task 4 ✓
- Wrapper shell short-circuit + venv-missing fail-open → Task 5 ✓
- `install-hook`/`uninstall-hook` idempotent + backup + venv check → Task 6 ✓
- Empirical `stop_hook_active` lifecycle characterization → Task 1 ✓
- Phase-1 consistency (time-expiry never blocks) → encoded in Task 3 `decide` + Task 2 state split ✓
- Global registration + live test → Task 7 ✓
- Out of scope (Phase 3 discovery, SubagentStop) → not present ✓

**Placeholder scan:** No TBD/TODO/"handle errors"/dead-code anywhere; every code step is complete and directly usable (Task 6 Step 4's earlier confusing reminder line was removed).

**Type consistency:** `capability_state(cap, entry, root, now)`, `BLOCK_STATES`, `GateDecision(block, reason, note)`, `decide(payload, now)`, `resolve_root`, `cmd_gate(stdin_text, now)`, `install_hook(settings_path, command=)`, `uninstall_hook(settings_path)`, `HOOK_TAG` are named identically everywhere they appear. `project.MANIFEST_NAME`/`LEDGER_REL`/`find_root` are introduced in Task 3 and used by `gate.py` and `cli.py` consistently.

---

## Next phase (separate plan)

- **Phase 3 — Discovery:** agent-driven spot→propose→approve of new capabilities into the manifest (the `ctk` skill behavior), the last piece that makes the loop self-populating.
