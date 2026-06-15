# Capability Verification — Phase 3 (Agent-Driven Discovery) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A code-backed `caps add` command that safely appends a proposed capability to `capabilities.yaml` (as a red, never-proven entry) and scaffolds a failing check stub, plus the `ctk` SKILL.md discovery behavior that drives spot→propose→approve.

**Architecture:** `caps add` builds the new manifest text in memory, validates it by re-parsing with `load_manifest` (requiring the new id to be present), backs up the existing file, then writes — never touching disk on a bad append. A failing pytest stub is scaffolded so a new capability can never be falsely proven. A shared `backup_file` helper is extracted from `hookinstall.py`. The discovery loop itself is agent behavior documented in SKILL.md.

**Tech Stack:** Python 3 stdlib + PyYAML + the existing `caps` package; pytest.

**Reference spec:** `docs/superpowers/specs/2026-06-15-capability-verification-discovery-design.md`

---

## File Structure

```
claude-test-kit/
├── caps/
│   ├── backup.py        # NEW: backup_file(path) (extracted from hookinstall._backup)
│   ├── hookinstall.py   # MODIFY: use backup_file
│   ├── manifest_edit.py # NEW: add_capability(...), ManifestEditError, stub scaffolder
│   └── cli.py           # MODIFY: `add` subcommand, dispatched before find_root precheck
├── SKILL.md             # MODIFY: discovery behavior (spot -> propose -> caps add -> verify)
└── tests/
    ├── unit/
    │   ├── test_caps_backup.py
    │   └── test_caps_manifest_edit.py
    └── integration/
        └── test_caps_add_cli.py
```

**Canonical names (keep identical):**
- `backup_file(path) -> Path` (in `caps/backup.py`).
- `add_capability(manifest_path, *, id, description, given, when, then, tier, deps, check=None, shell=None) -> None` (in `caps/manifest_edit.py`).
- `ManifestEditError` (in `caps/manifest_edit.py`).

Run tests with `.venv/bin/python -m pytest <path> -q`; full suite `./run_tests.sh`.

---

### Task 1: Shared `backup_file` helper

**Files:**
- Create: `caps/backup.py`
- Modify: `caps/hookinstall.py`
- Test: `tests/unit/test_caps_backup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_backup.py`:

```python
import pytest
from caps.backup import backup_file


@pytest.mark.unit
def test_backup_copies_content(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("hello")
    bak = backup_file(f)
    assert bak.exists()
    assert bak.read_text() == "hello"
    assert bak.name.startswith("settings.json.bak.")


@pytest.mark.unit
def test_backup_does_not_overwrite_same_day(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("a")
    b1 = backup_file(f)
    f.write_text("b")
    b2 = backup_file(f)
    assert b1 != b2          # second backup gets a -2 suffix
    assert b1.read_text() == "a"
    assert b2.read_text() == "b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_backup.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.backup'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/backup.py`:

```python
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Union


def backup_file(path: Union[str, Path]) -> Path:
    """Copy `path` to `path.bak.<YYYYMMDD>`, adding a -2, -3, ... suffix if a
    backup for today already exists. Returns the backup path."""
    path = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    candidate = path.with_suffix(path.suffix + f".bak.{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak.{stamp}-{n}")
        n += 1
    shutil.copy2(path, candidate)
    return candidate
```

- [ ] **Step 4: Refactor `hookinstall.py` to use it**

In `caps/hookinstall.py`: delete the local `_backup` function, add `from .backup import backup_file`, and replace the two `_backup(settings_path)` calls with `backup_file(settings_path)`. (The `import shutil` / `datetime` imports in hookinstall may now be unused — remove them if so.)

- [ ] **Step 5: Run tests to verify all pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_backup.py tests/unit/test_caps_hookinstall.py -q`
Expected: PASS (new backup tests + the existing hookinstall tests, which still see a `.bak.` file created).

- [ ] **Step 6: Commit**

```bash
git add caps/backup.py caps/hookinstall.py tests/unit/test_caps_backup.py
git commit -m "refactor(caps): extract shared backup_file helper"
```

---

### Task 2: `add_capability` (validate-in-memory-then-write + stub scaffold)

**Files:**
- Create: `caps/manifest_edit.py`
- Test: `tests/unit/test_caps_manifest_edit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_manifest_edit.py`:

```python
import pytest
from caps.manifest import load_manifest
from caps.manifest_edit import add_capability, ManifestEditError


def _common():
    return dict(description="writes rows and reads back", given="a db",
                when="the job runs", then="rows read back", tier="cheap")


@pytest.mark.unit
def test_creates_manifest_with_header_when_absent(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap1", deps=["ingest.py"],
                   check="checks/test_cap1.py::test_cap1", **_common())
    assert m.exists()
    caps = load_manifest(m)
    assert [c.id for c in caps] == ["cap1"]
    assert caps[0].check_kind == "pytest"


@pytest.mark.unit
def test_appends_and_preserves_comments_and_prior_entries(tmp_path):
    m = tmp_path / "capabilities.yaml"
    m.write_text(
        "# my hand-written note\n"
        "capabilities:\n"
        "  - id: existing\n"
        "    description: d\n"
        "    given: g\n"
        "    when: w\n"
        "    then: t\n"
        "    tier: cheap\n"
        "    deps: []\n"
        "    check: checks/a.py::t\n"
    )
    add_capability(m, id="cap2", deps=[],
                   check="checks/test_cap2.py::test_cap2", **_common())
    text = m.read_text()
    assert "# my hand-written note" in text          # comment preserved
    ids = [c.id for c in load_manifest(m)]
    assert ids == ["existing", "cap2"]               # both present


@pytest.mark.unit
def test_duplicate_id_rejected_and_file_unchanged(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="dup", deps=[], check="checks/t.py::t", **_common())
    before = m.read_text()
    with pytest.raises(ManifestEditError):
        add_capability(m, id="dup", deps=[], check="checks/t2.py::t", **_common())
    assert m.read_text() == before


@pytest.mark.unit
def test_non_block_style_rejected_and_file_unchanged(tmp_path):
    m = tmp_path / "capabilities.yaml"
    m.write_text("capabilities: []\n")
    before = m.read_text()
    with pytest.raises(ManifestEditError):
        add_capability(m, id="x", deps=[], check="checks/t.py::t", **_common())
    assert m.read_text() == before                   # disk untouched on bad append


@pytest.mark.unit
def test_scaffolds_failing_stub_when_check_file_absent(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap3", deps=[],
                   check="checks/test_cap3.py::test_cap3", **_common())
    stub = tmp_path / "checks" / "test_cap3.py"
    assert stub.exists()
    body = stub.read_text()
    assert "def test_cap3" in body
    assert "NotImplementedError" in body             # red by design


@pytest.mark.unit
def test_does_not_overwrite_existing_check_file(tmp_path):
    m = tmp_path / "capabilities.yaml"
    (tmp_path / "checks").mkdir()
    real = tmp_path / "checks" / "test_cap4.py"
    real.write_text("def test_cap4():\n    assert True  # real check\n")
    add_capability(m, id="cap4", deps=[],
                   check="checks/test_cap4.py::test_cap4", **_common())
    assert "real check" in real.read_text()          # untouched


@pytest.mark.unit
def test_shell_check_appended_without_scaffold(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap5", deps=[], shell="./prove.sh", **_common())
    cap = load_manifest(m)[0]
    assert cap.check_kind == "shell"
    assert cap.check_target == "./prove.sh"
    assert not (tmp_path / "prove.sh").exists()      # shell not scaffolded


@pytest.mark.unit
def test_backup_written_when_manifest_existed(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap6", deps=[], check="checks/t.py::t", **_common())
    add_capability(m, id="cap7", deps=[], check="checks/t7.py::t", **_common())
    assert list(tmp_path.glob("capabilities.yaml.bak.*"))


@pytest.mark.unit
def test_requires_exactly_one_of_check_or_shell(tmp_path):
    m = tmp_path / "capabilities.yaml"
    with pytest.raises(ManifestEditError):
        add_capability(m, id="z", deps=[], **_common())            # neither
    with pytest.raises(ManifestEditError):
        add_capability(m, id="z", deps=[], check="a::b", shell="x", **_common())  # both
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_manifest_edit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.manifest_edit'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/manifest_edit.py`:

```python
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional, Union

import yaml

from .manifest import load_manifest, ManifestError

HEADER = (
    "# Capabilities this project promises — managed with `caps`.\n"
    "# Prove with: python -m caps verify\n"
    "capabilities:\n"
)


class ManifestEditError(Exception):
    """Raised when a capability cannot be added safely; the manifest on disk is
    left unchanged."""


def _entry_block(entry: dict) -> str:
    """YAML for one capability, indented two spaces as a list item under
    `capabilities:`. safe_dump handles escaping of arbitrary scalar values."""
    dumped = yaml.safe_dump([entry], sort_keys=False, default_flow_style=False).rstrip("\n")
    return "\n".join(("  " + line) if line else line for line in dumped.split("\n"))


def _validate_candidate(candidate_text: str, new_id: str) -> None:
    """Parse the candidate manifest in a temp file. Accept only if it parses AND
    contains new_id. Raises ManifestEditError otherwise (disk never touched)."""
    tmp = Path(tempfile.mkstemp(suffix=".yaml")[1])
    try:
        tmp.write_text(candidate_text)
        try:
            caps = load_manifest(tmp)
        except ManifestError as e:
            raise ManifestEditError(
                f"appended entry produced an invalid manifest ({e}); "
                f"is `capabilities:` block-style? manifest unchanged"
            ) from e
    finally:
        tmp.unlink(missing_ok=True)
    if new_id not in [c.id for c in caps]:
        raise ManifestEditError(
            f"appended entry for {new_id!r} did not load as a list item "
            f"(check `capabilities:` is a block-style list); manifest unchanged"
        )


def _scaffold_stub(root: Path, check: str, cap_id: str) -> None:
    rel, _, test_name = check.partition("::")
    test_name = test_name or "test_capability"
    target = root / rel
    if target.exists():
        return  # never clobber a real check
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Scaffolded by `caps add` — replace with a real "
        "write -> readback -> teardown check.\n"
        f"def {test_name}():\n"
        f'    raise NotImplementedError("implement the capability check for {cap_id}")\n'
    )


def add_capability(
    manifest_path: Union[str, Path],
    *,
    id: str,
    description: str,
    given: str,
    when: str,
    then: str,
    tier: str,
    deps: list[str],
    check: Optional[str] = None,
    shell: Optional[str] = None,
) -> None:
    manifest_path = Path(manifest_path)
    if (check is None) == (shell is None):
        raise ManifestEditError("provide exactly one of check= or shell=")

    # Duplicate id?
    if manifest_path.exists():
        existing = load_manifest(manifest_path)
        if id in [c.id for c in existing]:
            raise ManifestEditError(f"capability id {id!r} already exists")
        existing_text = manifest_path.read_text()
        if not existing_text.endswith("\n"):
            existing_text += "\n"
    else:
        existing_text = HEADER

    entry: dict = {"id": id, "description": description, "given": given,
                   "when": when, "then": then, "tier": tier, "deps": list(deps)}
    entry["check"] = check if check is not None else {"shell": shell}

    candidate_text = existing_text + _entry_block(entry) + "\n"
    _validate_candidate(candidate_text, id)   # raises if bad; disk still untouched

    if manifest_path.exists():
        from .backup import backup_file
        backup_file(manifest_path)
    manifest_path.write_text(candidate_text)

    if check is not None:
        _scaffold_stub(manifest_path.parent, check, id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_manifest_edit.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/manifest_edit.py tests/unit/test_caps_manifest_edit.py
git commit -m "feat(caps): add_capability — validate-in-memory-then-write + failing stub scaffold"
```

---

### Task 3: `caps add` CLI subcommand

**Files:**
- Modify: `caps/cli.py`
- Test: append to `tests/unit/test_caps_cli.py`, create `tests/integration/test_caps_add_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_caps_cli.py`:

```python
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
```

Create `tests/integration/test_caps_add_cli.py`:

```python
import os
import sys
import json
from pathlib import Path

import pytest
import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]


def _caps(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run([sys.executable, "-m", "caps", *args], cwd=str(cwd), env=env)


@pytest.mark.integration
def test_add_then_verify_is_red_then_green(tmp_path):
    add = _caps([
        "add", "--id", "raw", "--tier", "cheap",
        "--description", "d", "--given", "g", "--when", "w", "--then", "t",
        "--check", "checks/test_raw.py::test_raw",
    ], tmp_path)
    assert add.returncode == 0, add.stdout + add.stderr

    # status: never proven
    st = _caps(["status"], tmp_path)
    assert "never proven" in st.stdout.lower()

    # INTEGRITY: a scaffolded capability cannot be proven — verify is red.
    v1 = _caps(["verify", "--capability", "raw"], tmp_path)
    assert v1.returncode != 0, v1.stdout + v1.stderr

    # Replace the stub with a real passing check -> verify goes green.
    (tmp_path / "checks" / "test_raw.py").write_text("def test_raw():\n    assert True\n")
    v2 = _caps(["verify", "--capability", "raw"], tmp_path)
    assert v2.returncode == 0, v2.stdout + v2.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py::test_add_creates_never_proven_capability tests/integration/test_caps_add_cli.py -q`
Expected: FAIL — `add` is not a registered subcommand.

- [ ] **Step 3: Write minimal implementation**

In `caps/cli.py`, add import:

```python
from .manifest_edit import add_capability, ManifestEditError
```

Register the subparser alongside the others:

```python
    ad = sub.add_parser("add", help="add a capability to the manifest (never-proven)")
    ad.add_argument("--id", required=True)
    ad.add_argument("--description", required=True)
    ad.add_argument("--given", required=True)
    ad.add_argument("--when", required=True)
    ad.add_argument("--then", required=True)
    ad.add_argument("--tier", required=True, choices=["cheap", "live"])
    ad.add_argument("--deps", action="append", default=[],
                    help="dep glob (repeat for multiple)")
    grp = ad.add_mutually_exclusive_group(required=True)
    grp.add_argument("--check", help="pytest node, e.g. checks/test_x.py::test_x")
    grp.add_argument("--shell", help="shell command; exit 0 = proven")
    ad.add_argument("--manifest", default=None, help="path to capabilities.yaml")
```

Dispatch `add` next to `gate`/`install-hook` (before the `find_root` precheck), since it must work even when no manifest exists yet. Add immediately after the `install-hook`/`uninstall-hook` dispatch block:

```python
    if args.command == "add":
        if args.manifest:
            manifest_path = Path(args.manifest)
        else:
            start = Path(cwd) if cwd else Path.cwd()
            manifest_path = (find_root(start) or start) / MANIFEST_NAME
        try:
            add_capability(
                manifest_path, id=args.id, description=args.description,
                given=args.given, when=args.when, then=args.then,
                tier=args.tier, deps=args.deps, check=args.check, shell=args.shell,
            )
        except (ManifestEditError, ManifestError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"added capability {args.id!r} (never-proven) -> {manifest_path}")
        return 0
```

(`ManifestError` is already imported in cli.py from Task earlier work; `find_root`/`MANIFEST_NAME` come from `.project`.)

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_caps_cli.py tests/integration/test_caps_add_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add caps/cli.py tests/unit/test_caps_cli.py tests/integration/test_caps_add_cli.py
git commit -m "feat(caps): caps add subcommand (proposes a never-proven capability)"
```

---

### Task 4: SKILL.md discovery behavior + full suite

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Update the discovery section**

In `SKILL.md`, find the capability-layer section's discovery guidance (the bullet about proposing a capability when there's no matching check) and replace/extend it with the concrete `caps add` loop. Insert this block under the capability section:

```markdown
### Discovery — propose capabilities for un-covered work

When you do or see capability-shaped work (write to a DB, deploy, create a
table/file/endpoint) in a project that has **no matching capability**, don't move
on silently — **propose one**, then on the user's approval wire it in with
`caps add` (never hand-edit `capabilities.yaml`):

1. Surface a concrete proposal: `id`, `given`/`when`/`then`, `tier`
   (cheap=local/fast, live=needs real infra), `deps` (globs of the code it
   exercises), and the check.
2. On **yes**, run it in (this creates the entry as **never-proven** and scaffolds
   a failing stub — it cannot fake a pass):
   ```
   python -m caps add --id <id> --tier <cheap|live> \
     --description "..." --given "..." --when "..." --then "..." \
     --deps <glob> [--deps <glob> ...] \
     --check checks/test_<id>.py::test_<id>      # or --shell "./prove.sh"
   ```
3. Implement the scaffolded check body (the real write → readback → teardown).
4. Run `python -m caps verify --capability <id>` to actually prove it.

You are advisory — never add a capability without the user's explicit yes.
```

If the older, vaguer "propose adding one to the manifest" wording exists elsewhere in the file, leave it or tighten it to point here — don't duplicate the steps.

- [ ] **Step 2: Full suite green**

Run: `./run_tests.sh`
Expected: ALL tests pass (Phase 1 + 2 + 3).

- [ ] **Step 3: Manual smoke (optional but recommended)**

```bash
cd /tmp && rm -rf addtest && mkdir addtest && cd addtest
PYTHONPATH=/Users/stuart.gano/Documents/claude-test-kit \
  /Users/stuart.gano/Documents/claude-test-kit/.venv/bin/python -m caps add \
  --id demo --tier cheap --description d --given g --when w --then t \
  --check checks/test_demo.py::test_demo
# expect: capabilities.yaml created, checks/test_demo.py scaffolded with NotImplementedError
PYTHONPATH=/Users/stuart.gano/Documents/claude-test-kit \
  /Users/stuart.gano/Documents/claude-test-kit/.venv/bin/python -m caps status
# expect: demo  never proven
```

- [ ] **Step 4: Commit**

```bash
cd /Users/stuart.gano/Documents/claude-test-kit
git add SKILL.md
git commit -m "docs(skill): caps add discovery loop (spot -> propose -> add -> verify)"
```

---

## Self-Review

**Spec coverage:**
- `caps add` flag-driven, code-backed append → Task 3 ✓
- Validate-in-memory-then-write, accept only if parses AND new id present → Task 2 (`_validate_candidate`) ✓
- Duplicate id rejected, disk untouched → Task 2 ✓
- Non-block-style/empty `capabilities:` rejected, disk untouched → Task 2 ✓
- Failing stub scaffolded (pytest, file-absent only; never clobber; shell not scaffolded) → Task 2 ✓
- Back up before write (shared helper) → Task 1 + Task 2 ✓
- Manifest created-with-header when absent → Task 2 ✓
- SKILL.md spot→propose→approve→add→verify behavior → Task 4 ✓
- Integrity: add then verify exits non-zero, state fail/never-proven → Task 3 integration ✓
- Out of scope (PostToolUse nudge, gate/runner changes) → absent ✓

**Placeholder scan:** No TBD/TODO/"handle errors"; every code step is complete. The `<id>`/`<glob>` tokens appear only inside the SKILL.md template the user is meant to fill at runtime, not as plan placeholders.

**Type consistency:** `backup_file(path)`, `add_capability(manifest_path, *, id, description, given, when, then, tier, deps, check=None, shell=None)`, `ManifestEditError`, and the helpers `_entry_block`/`_validate_candidate`/`_scaffold_stub` are named identically across tasks. `caps add` flags map exactly to `add_capability` kwargs.

---

## Done

With Phase 3 merged, the full loop is self-sustaining: discover → declare (`caps add`, red) → implement → verify (green) → enforce (Stop hook). No further phases planned.
