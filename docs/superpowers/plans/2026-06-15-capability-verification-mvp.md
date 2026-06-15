# Capability Verification — Phase 1 (MVP Manual Runner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the manual, hand-run core of capability verification — a `caps` package that loads a `capabilities.yaml` manifest, runs each capability's check (pytest node or shell command), records proof in a committed ledger with tier-aware freshness, and exposes a `verify` / `status` / `ack` CLI. No Stop hook, no discovery yet (those are Phases 2 and 3).

**Architecture:** A new sibling package `caps/` lives alongside the pure `ctk/` primitive library in the same repo. `caps` *uses* `ctk` (notably `ctk.run`) but `ctk/` is not modified. Each unit is one focused module: manifest parsing, fingerprinting, ledger I/O, freshness logic, the check runner, and a thin argparse CLI. The MVP is fully usable run by hand (`python -m caps verify`) and is what Phase 2's hook will later call.

**Tech Stack:** Python 3 (stdlib + dataclasses), PyYAML for the manifest, pytest + `ctk` for tests, `ctk.run` for subprocess execution.

**Reference spec:** `docs/superpowers/specs/2026-06-15-capability-verification-design.md`

---

## File Structure

```
claude-test-kit/
├── caps/                              # NEW capability layer (sibling to ctk/)
│   ├── __init__.py                    # public exports
│   ├── manifest.py                    # load/validate capabilities.yaml -> [Capability]
│   ├── fingerprint.py                 # hash check file + deps globs
│   ├── ledger.py                      # read/write .ctk/ledger.json (LedgerEntry)
│   ├── freshness.py                   # parse_duration, is_fresh, waiver_active
│   ├── runner.py                      # run_capability -> "pass"|"fail"|"error"
│   ├── cli.py                         # argparse: verify / status / ack
│   └── __main__.py                    # `python -m caps` -> cli.main()
├── tests/
│   ├── unit/
│   │   ├── test_caps_manifest.py
│   │   ├── test_caps_fingerprint.py
│   │   ├── test_caps_ledger.py
│   │   ├── test_caps_freshness.py
│   │   ├── test_caps_runner.py
│   │   └── test_caps_cli.py
│   └── integration/
│       └── test_caps_end_to_end.py    # broken capability blocks; fixed passes
└── requirements.txt                   # + PyYAML
```

**Canonical types (used across tasks — keep names identical):**

- `Capability` (dataclass): `id, description, given, when, then, tier, deps, freshness, check_kind, check_target, warnings`
  - `tier` ∈ `{"cheap","live"}`; `check_kind` ∈ `{"pytest","shell"}`; `freshness` is `"code"` or a duration like `"24h"`.
- `LedgerEntry` (dataclass): `result, at, tier, fingerprint=None, waiver=None`
  - `result` ∈ `{"pass","fail","error","waived"}`; `at` is an ISO-8601 string; `waiver` is `None` or `{"reason": str, "until": isostr}`.

---

### Task 1: Project setup — dependency + package skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `caps/__init__.py`

- [ ] **Step 1: Add PyYAML to requirements**

Edit `requirements.txt` so the first lines read:

```
pytest>=7.4
PyYAML>=6.0
```

- [ ] **Step 2: Install it into the dev venv**

Run: `./run_tests.sh unit`
Expected: the runner installs PyYAML (and pytest), then runs the existing unit suite — all PASS. This confirms the toolchain works before we add code.

- [ ] **Step 3: Create the package marker with public exports**

Create `caps/__init__.py`:

```python
"""
caps — the capability-verification layer for claude-test-kit.

Declares the capabilities a project promises (capabilities.yaml), proves them
against reality, and records proof in a committed ledger. Built on ctk.

Phase 1 (this package) is the manual runner: `python -m caps verify`.
"""

from .manifest import Capability, load_manifest, ManifestError
from .ledger import LedgerEntry, load_ledger, save_ledger
from .fingerprint import fingerprint
from .freshness import parse_duration, is_fresh, waiver_active, FreshnessError
from .runner import run_capability

__all__ = [
    "Capability",
    "load_manifest",
    "ManifestError",
    "LedgerEntry",
    "load_ledger",
    "save_ledger",
    "fingerprint",
    "parse_duration",
    "is_fresh",
    "waiver_active",
    "FreshnessError",
    "run_capability",
]

__version__ = "0.1.0"
```

Note: this imports modules created in later tasks. It will not import cleanly until Task 6 is done — that is expected. Do not run `import caps` until then; the per-module unit tests in Tasks 2–6 import their specific module directly.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt caps/__init__.py
git commit -m "feat(caps): scaffold capability layer package + PyYAML dep"
```

---

### Task 2: Manifest model and loader

**Files:**
- Create: `caps/manifest.py`
- Test: `tests/unit/test_caps_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_manifest.py`:

```python
import textwrap
import pytest
from caps.manifest import load_manifest, ManifestError


def _write(tmp_path, body: str):
    p = tmp_path / "capabilities.yaml"
    p.write_text(textwrap.dedent(body))
    return p


@pytest.mark.unit
def test_loads_pytest_check_with_defaults(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: writes-db
            description: writes rows and reads them back
            given: a reachable db
            when: the job runs
            then: rows read back
            tier: live
            deps: [ingest.py]
            check: checks/test_db.py::test_write_readback
    """)
    caps = load_manifest(p)
    assert len(caps) == 1
    c = caps[0]
    assert c.id == "writes-db"
    assert c.tier == "live"
    assert c.deps == ["ingest.py"]
    assert c.check_kind == "pytest"
    assert c.check_target == "checks/test_db.py::test_write_readback"
    # live default freshness is time-based
    assert c.freshness == "24h"
    assert c.warnings == []


@pytest.mark.unit
def test_cheap_default_freshness_is_code(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: parses-output
            description: parses
            given: a file
            when: parse runs
            then: structured output
            tier: cheap
            deps: ["src/**"]
            check: checks/test_parse.py::test_it
    """)
    assert load_manifest(p)[0].freshness == "code"


@pytest.mark.unit
def test_shell_check(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: deploy-live
            description: deploy is live
            given: creds
            when: deploy runs
            then: app responds
            tier: live
            deps: [app.yaml]
            check:
              shell: ./scripts/prove_deploy.sh app
    """)
    c = load_manifest(p)[0]
    assert c.check_kind == "shell"
    assert c.check_target == "./scripts/prove_deploy.sh app"


@pytest.mark.unit
def test_missing_deps_produces_warning(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: no-deps
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/test_x.py::test_x
    """)
    c = load_manifest(p)[0]
    assert c.deps == []
    assert any("deps" in w for w in c.warnings)


@pytest.mark.unit
def test_bad_tier_raises(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: bad
            description: x
            given: g
            when: w
            then: t
            tier: medium
            check: checks/test_x.py::test_x
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)


@pytest.mark.unit
def test_duplicate_ids_raise(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: dup
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/a.py::t
          - id: dup
            description: y
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/b.py::t
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)


@pytest.mark.unit
def test_missing_required_field_raises(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: incomplete
            description: x
            tier: cheap
            check: checks/x.py::t
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.manifest'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/manifest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

import yaml


class ManifestError(Exception):
    """Raised when capabilities.yaml is malformed or invalid."""


VALID_TIERS = ("cheap", "live")
DEFAULT_FRESHNESS = {"cheap": "code", "live": "24h"}
REQUIRED_FIELDS = ("id", "description", "given", "when", "then", "tier", "check")


@dataclass
class Capability:
    id: str
    description: str
    given: str
    when: str
    then: str
    tier: str
    deps: list[str]
    freshness: str
    check_kind: str          # "pytest" | "shell"
    check_target: str
    warnings: list[str] = field(default_factory=list)


def _parse_check(raw: Union[str, dict], cap_id: str) -> tuple[str, str]:
    if isinstance(raw, str):
        return "pytest", raw
    if isinstance(raw, dict) and list(raw.keys()) == ["shell"]:
        return "shell", str(raw["shell"])
    raise ManifestError(
        f"capability {cap_id!r}: 'check' must be a pytest node string "
        f"or a single-key mapping {{shell: ...}}"
    )


def load_manifest(path: Union[str, Path]) -> list[Capability]:
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ManifestError(f"could not parse {path}: {e}") from e

    entries = doc.get("capabilities")
    if not isinstance(entries, list):
        raise ManifestError("manifest must have a top-level 'capabilities:' list")

    caps: list[Capability] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ManifestError(f"each capability must be a mapping, got {raw!r}")
        missing = [f for f in REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ManifestError(
                f"capability {raw.get('id', '<no id>')!r} missing fields: {missing}"
            )
        cid = str(raw["id"])
        if cid in seen:
            raise ManifestError(f"duplicate capability id: {cid!r}")
        seen.add(cid)

        tier = str(raw["tier"])
        if tier not in VALID_TIERS:
            raise ManifestError(
                f"capability {cid!r}: tier must be one of {VALID_TIERS}, got {tier!r}"
            )

        check_kind, check_target = _parse_check(raw["check"], cid)

        warnings: list[str] = []
        deps = raw.get("deps")
        if deps is None:
            deps = []
            warnings.append(
                "deps not declared; code-freshness covers only the check file"
            )
        elif not isinstance(deps, list):
            raise ManifestError(f"capability {cid!r}: deps must be a list of globs")
        deps = [str(d) for d in deps]

        freshness = str(raw.get("freshness", DEFAULT_FRESHNESS[tier]))

        caps.append(
            Capability(
                id=cid,
                description=str(raw["description"]),
                given=str(raw["given"]),
                when=str(raw["when"]),
                then=str(raw["then"]),
                tier=tier,
                deps=deps,
                freshness=freshness,
                check_kind=check_kind,
                check_target=check_target,
                warnings=warnings,
            )
        )
    return caps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_manifest.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/manifest.py tests/unit/test_caps_manifest.py
git commit -m "feat(caps): manifest model + loader with tier-aware freshness defaults"
```

---

### Task 3: Fingerprint

**Files:**
- Create: `caps/fingerprint.py`
- Test: `tests/unit/test_caps_fingerprint.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_fingerprint.py`:

```python
import pytest
from caps.manifest import Capability
from caps.fingerprint import fingerprint


def _cap(**kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="pytest", check_target="checks/test_x.py::test_x",
    )
    base.update(kw)
    return Capability(**base)


@pytest.mark.unit
def test_fingerprint_changes_when_dep_changes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "ingest.py").write_text("x = 1\n")
    cap = _cap(deps=["ingest.py"])

    fp1 = fingerprint(cap, tmp_path)
    (tmp_path / "ingest.py").write_text("x = 2\n")
    fp2 = fingerprint(cap, tmp_path)

    assert fp1 != fp2
    assert fp1.startswith("sha256:")


@pytest.mark.unit
def test_fingerprint_stable_when_nothing_changes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    cap = _cap()
    assert fingerprint(cap, tmp_path) == fingerprint(cap, tmp_path)


@pytest.mark.unit
def test_glob_deps_matched(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.py").write_text("a = 1\n")
    cap = _cap(deps=["lib/**"])

    fp1 = fingerprint(cap, tmp_path)
    (tmp_path / "lib" / "a.py").write_text("a = 99\n")
    assert fingerprint(cap, tmp_path) != fp1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_fingerprint.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.fingerprint'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/fingerprint.py`:

```python
from __future__ import annotations

import glob
import hashlib
from pathlib import Path
from typing import Union

from .manifest import Capability


def _collect_files(capability: Capability, root: Path) -> list[Path]:
    files: list[Path] = []
    # The check file itself (pytest node "path::test" -> "path"). Shell checks
    # have no single source file, so only their deps are hashed.
    if capability.check_kind == "pytest":
        files.append(root / capability.check_target.split("::", 1)[0])
    for pattern in capability.deps:
        for match in glob.glob(str(root / pattern), recursive=True):
            files.append(Path(match))
    return files


def fingerprint(capability: Capability, root: Union[str, Path]) -> str:
    """Hash the check file plus every file matched by deps globs.

    Deterministic: files are sorted by their path relative to root. A missing
    file hashes as a literal "<missing>" marker so deletion changes the result.
    """
    root = Path(root)
    h = hashlib.sha256()
    rels = sorted(
        {str(f.relative_to(root)) if f.is_absolute() and root in f.parents or f.is_absolute()
         else str(f) for f in _collect_files(capability, root)}
    )
    for rel in rels:
        p = root / rel
        h.update(rel.encode())
        if p.is_file():
            h.update(p.read_bytes())
        else:
            h.update(b"<missing>")
    return "sha256:" + h.hexdigest()
```

Note: the relative-path expression above can be simplified — keep it robust. If you prefer clarity, replace the `rels = sorted({...})` block with:

```python
    seen: set[str] = set()
    for f in _collect_files(capability, root):
        f = f if f.is_absolute() else (root / f)
        try:
            rel = str(f.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(f)
        seen.add(rel)
    for rel in sorted(seen):
        p = root / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.is_file() else b"<missing>")
```

Use the clearer version.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_fingerprint.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/fingerprint.py tests/unit/test_caps_fingerprint.py
git commit -m "feat(caps): content fingerprint over check file + deps globs"
```

---

### Task 4: Ledger

**Files:**
- Create: `caps/ledger.py`
- Test: `tests/unit/test_caps_ledger.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_ledger.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.ledger'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/ledger.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass
class LedgerEntry:
    result: str                       # "pass" | "fail" | "error" | "waived"
    at: str                           # ISO-8601 timestamp
    tier: str                         # "cheap" | "live"
    fingerprint: Optional[str] = None
    waiver: Optional[dict] = None     # {"reason": str, "until": isostr}


def load_ledger(path: Union[str, Path]) -> dict[str, LedgerEntry]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text() or "{}")
    return {k: LedgerEntry(**v) for k, v in raw.items()}


def save_ledger(path: Union[str, Path], entries: dict[str, LedgerEntry]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_ledger.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/ledger.py tests/unit/test_caps_ledger.py
git commit -m "feat(caps): committed JSON ledger (LedgerEntry round-trip)"
```

---

### Task 5: Freshness and waivers

**Files:**
- Create: `caps/freshness.py`
- Test: `tests/unit/test_caps_freshness.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_freshness.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from caps.manifest import Capability
from caps.ledger import LedgerEntry
from caps.freshness import parse_duration, is_fresh, waiver_active, FreshnessError


def _cap(**kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="pytest", check_target="checks/x.py::t",
    )
    base.update(kw)
    return Capability(**base)


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_parse_duration():
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2d") == timedelta(days=2)
    with pytest.raises(FreshnessError):
        parse_duration("soon")


@pytest.mark.unit
def test_code_freshness_matches_fingerprint():
    cap = _cap(freshness="code")
    entry = LedgerEntry(result="pass", at=NOW.isoformat(), tier="cheap",
                        fingerprint="sha256:aaa")
    assert is_fresh(cap, entry, "sha256:aaa", NOW) is True
    assert is_fresh(cap, entry, "sha256:bbb", NOW) is False


@pytest.mark.unit
def test_time_freshness_expires():
    cap = _cap(tier="live", freshness="24h")
    recent = LedgerEntry(result="pass", at=(NOW - timedelta(hours=1)).isoformat(),
                         tier="live")
    stale = LedgerEntry(result="pass", at=(NOW - timedelta(hours=25)).isoformat(),
                        tier="live")
    assert is_fresh(cap, recent, "ignored", NOW) is True
    assert is_fresh(cap, stale, "ignored", NOW) is False


@pytest.mark.unit
def test_non_pass_is_never_fresh():
    cap = _cap(freshness="code")
    entry = LedgerEntry(result="fail", at=NOW.isoformat(), tier="cheap",
                        fingerprint="sha256:aaa")
    assert is_fresh(cap, entry, "sha256:aaa", NOW) is False
    assert is_fresh(cap, None, "sha256:aaa", NOW) is False


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_freshness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.freshness'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/freshness.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from .ledger import LedgerEntry
from .manifest import Capability


class FreshnessError(Exception):
    """Raised on an unparseable freshness/duration value."""


_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(s: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", s)
    if not m:
        raise FreshnessError(f"bad duration {s!r}; expected forms like '24h', '30m', '2d'")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


def is_fresh(
    capability: Capability,
    entry: Optional[LedgerEntry],
    current_fingerprint: str,
    now: datetime,
) -> bool:
    """True iff the recorded proof is a pass AND still trustworthy.

    code freshness: fingerprint must match the current code.
    duration freshness: the pass must be within the window.
    """
    if entry is None or entry.result != "pass":
        return False
    if capability.freshness == "code":
        return entry.fingerprint == current_fingerprint
    window = parse_duration(capability.freshness)
    return (now - datetime.fromisoformat(entry.at)) < window


def waiver_active(entry: Optional[LedgerEntry], now: datetime) -> bool:
    if entry is None or not entry.waiver:
        return False
    return now < datetime.fromisoformat(entry.waiver["until"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_freshness.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/freshness.py tests/unit/test_caps_freshness.py
git commit -m "feat(caps): freshness (code fingerprint + time window) and waiver checks"
```

---

### Task 6: Check runner

**Files:**
- Create: `caps/runner.py`
- Test: `tests/unit/test_caps_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_runner.py`:

```python
import pytest
from caps.manifest import Capability
from caps.runner import run_capability


def _cap(**kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="shell", check_target="true",
    )
    base.update(kw)
    return Capability(**base)


@pytest.mark.unit
def test_shell_pass(tmp_path):
    assert run_capability(_cap(check_target="exit 0"), tmp_path) == "pass"


@pytest.mark.unit
def test_shell_fail(tmp_path):
    assert run_capability(_cap(check_target="exit 1"), tmp_path) == "fail"


@pytest.mark.unit
def test_shell_error_convention_exit_3(tmp_path):
    # Exit 3 is the reserved "could not run / unreachable" signal.
    assert run_capability(_cap(check_target="exit 3"), tmp_path) == "error"


@pytest.mark.unit
def test_pytest_pass(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    cap = _cap(check_kind="pytest", check_target="checks/test_ok.py::test_ok")
    assert run_capability(cap, tmp_path) == "pass"


@pytest.mark.unit
def test_pytest_fail(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    cap = _cap(check_kind="pytest", check_target="checks/test_bad.py::test_bad")
    assert run_capability(cap, tmp_path) == "fail"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.runner'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/runner.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from typing import Union

import ctk
from .manifest import Capability

# Reserved shell exit code meaning "could not run / resource unreachable".
ERROR_EXIT = 3


def run_capability(capability: Capability, root: Union[str, Path]) -> str:
    """Execute the check and classify the outcome: 'pass' | 'fail' | 'error'.

    pytest: exit 0 -> pass, 1 -> fail, anything else (collection/internal error,
            no tests) -> error.
    shell:  exit 0 -> pass, ERROR_EXIT (3) -> error, any other non-zero -> fail.
    """
    root = str(root)
    if capability.check_kind == "pytest":
        r = ctk.run(
            [sys.executable, "-m", "pytest", capability.check_target, "-q", "-p", "no:cacheprovider"],
            cwd=root,
        )
        if r.returncode == 0:
            return "pass"
        if r.returncode == 1:
            return "fail"
        return "error"

    # shell
    r = ctk.run(capability.check_target, cwd=root)
    if r.returncode == 0:
        return "pass"
    if r.returncode == ERROR_EXIT:
        return "error"
    return "fail"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_runner.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify the package now imports cleanly**

Run: `python -c "import caps; print(caps.__version__)"`
Expected: prints `0.1.0` (all submodules referenced by `caps/__init__.py` now exist).

- [ ] **Step 6: Commit**

```bash
git add caps/runner.py tests/unit/test_caps_runner.py
git commit -m "feat(caps): check runner (pytest + shell) with pass/fail/error classification"
```

---

### Task 7: CLI — `status` (read-only) + project root discovery + entry point

**Files:**
- Create: `caps/cli.py`
- Create: `caps/__main__.py`
- Test: `tests/unit/test_caps_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_caps_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'caps.cli'`.

- [ ] **Step 3: Write minimal implementation**

Create `caps/cli.py`:

```python
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .manifest import load_manifest
from .ledger import load_ledger
from .fingerprint import fingerprint
from .freshness import is_fresh, waiver_active

MANIFEST_NAME = "capabilities.yaml"
LEDGER_REL = Path(".ctk") / "ledger.json"


def find_root(start: Path) -> Optional[Path]:
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / MANIFEST_NAME).is_file():
            return d
    return None


def _status_label(cap, entry, root, now) -> str:
    if waiver_active(entry, now):
        return "waived"
    if entry is None:
        return "never proven"
    if entry.result in ("fail", "error"):
        return entry.result
    fp = fingerprint(cap, root) if cap.freshness == "code" else ""
    return "proven" if is_fresh(cap, entry, fp, now) else "stale"


def cmd_status(root: Path, now: datetime) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    ledger = load_ledger(root / LEDGER_REL)
    glyph = {"proven": "OK ", "stale": "STALE", "fail": "FAIL",
             "error": "ERR ", "waived": "WAIV", "never proven": "----"}
    for cap in caps:
        label = _status_label(cap, ledger.get(cap.id), root, now)
        print(f"[{glyph.get(label, '?'):5}] {cap.id:30} {label}")
    return 0


def main(argv=None, cwd: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="caps")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show capability status (read-only)")

    args = parser.parse_args(argv)
    start = Path(cwd) if cwd else Path.cwd()
    root = find_root(start)
    if root is None:
        print(f"error: no {MANIFEST_NAME} found from {start}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    if args.command == "status":
        return cmd_status(root, now)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `caps/__main__.py`:

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/cli.py caps/__main__.py tests/unit/test_caps_cli.py
git commit -m "feat(caps): CLI skeleton with root discovery and read-only status"
```

---

### Task 8: CLI — `verify`

**Files:**
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_cli.py` (add cases)

- [ ] **Step 1: Write the failing test (append to the existing file)**

Add to `tests/unit/test_caps_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: FAIL — `verify` is not a registered subcommand (argparse error / SystemExit).

- [ ] **Step 3: Write minimal implementation**

In `caps/cli.py`, add imports at the top:

```python
from .runner import run_capability
from .ledger import LedgerEntry, save_ledger
```

Add this function above `main`:

```python
def cmd_verify(root: Path, now: datetime, only: Optional[str]) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    if only is not None:
        caps = [c for c in caps if c.id == only]
        if not caps:
            print(f"error: no capability with id {only!r}", file=sys.stderr)
            return 2

    ledger = load_ledger(root / LEDGER_REL)
    worst_ok = True
    for cap in caps:
        result = run_capability(cap, root)
        ledger[cap.id] = LedgerEntry(
            result=result,
            at=now.isoformat(),
            tier=cap.tier,
            fingerprint=fingerprint(cap, root) if cap.freshness == "code" else None,
            waiver=None,
        )
        print(f"{cap.id}: {result}")
        if result != "pass":
            worst_ok = False
    save_ledger(root / LEDGER_REL, ledger)
    return 0 if worst_ok else 1
```

In `main`, register the subparser (after the `status` subparser line):

```python
    v = sub.add_parser("verify", help="run checks and record proof")
    v.add_argument("--capability", dest="only", default=None,
                   help="verify a single capability by id")
```

And dispatch (before `return 2` at the end of `main`):

```python
    if args.command == "verify":
        return cmd_verify(root, now, args.only)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: PASS (5 passed — 2 from Task 7, 3 new).

- [ ] **Step 5: Commit**

```bash
git add caps/cli.py tests/unit/test_caps_cli.py
git commit -m "feat(caps): verify command runs checks and records the ledger"
```

---

### Task 9: CLI — `ack` (time-boxed waiver)

**Files:**
- Modify: `caps/cli.py`
- Test: `tests/unit/test_caps_cli.py` (add cases)

- [ ] **Step 1: Write the failing test (append)**

Add to `tests/unit/test_caps_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: FAIL — `ack` is not a registered subcommand.

- [ ] **Step 3: Write minimal implementation**

In `caps/cli.py`, add to the imports already present:

```python
from .freshness import parse_duration  # add alongside is_fresh, waiver_active
```

Add this function above `main`:

```python
def cmd_ack(root: Path, now: datetime, cap_id: str, reason: str, for_: str) -> int:
    caps = {c.id: c for c in load_manifest(root / MANIFEST_NAME)}
    if cap_id not in caps:
        print(f"error: no capability with id {cap_id!r}", file=sys.stderr)
        return 2
    until = (now + parse_duration(for_)).isoformat()
    ledger = load_ledger(root / LEDGER_REL)
    ledger[cap_id] = LedgerEntry(
        result="waived",
        at=now.isoformat(),
        tier=caps[cap_id].tier,
        fingerprint=None,
        waiver={"reason": reason, "until": until},
    )
    save_ledger(root / LEDGER_REL, ledger)
    print(f"{cap_id}: waived until {until} ({reason})")
    return 0
```

In `main`, register the subparser:

```python
    a = sub.add_parser("ack", help="record a time-boxed waiver for a capability")
    a.add_argument("capability", help="capability id to waive")
    a.add_argument("--reason", required=True, help="why it can't be proven now")
    a.add_argument("--for", dest="for_", default="24h",
                   help="waiver duration, e.g. 24h (default), 2d, 30m")
```

And dispatch (before the final `return 2`):

```python
    if args.command == "ack":
        return cmd_ack(root, now, args.capability, args.reason, args.for_)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_caps_cli.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add caps/cli.py tests/unit/test_caps_cli.py
git commit -m "feat(caps): ack command records time-boxed waivers"
```

---

### Task 10: End-to-end integration — broken capability blocks, fixed passes

**Files:**
- Create: `tests/integration/test_caps_end_to_end.py`

This proves the whole MVP through the real CLI as a subprocess: a project whose
capability check is initially broken makes `python -m caps verify` exit non-zero;
fixing the check makes it exit zero — the manual analogue of the future gate.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_caps_end_to_end.py`:

```python
import os
import sys
import textwrap
from pathlib import Path

import pytest
import ctk

# Repo root = two levels up from tests/integration/.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_caps(args, cwd):
    env = dict(os.environ)
    # Make `caps` and `ctk` importable in the subprocess running in a temp cwd.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run([sys.executable, "-m", "caps", *args], cwd=str(cwd), env=env)


@pytest.mark.integration
def test_broken_capability_blocks_then_fixed_passes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent("""
        capabilities:
          - id: word-count-writes-output
            description: the tool writes a parseable count file
            given: an input file
            when: the tool runs
            then: the output file exists and parses
            tier: cheap
            deps: [tool.py]
            check: checks/test_output.py::test_output
    """))
    # A check that asserts the tool produced output.
    (tmp_path / "checks" / "test_output.py").write_text(textwrap.dedent("""
        from pathlib import Path
        def test_output():
            assert Path("out.txt").read_text().strip() == "3"
    """))

    # Broken tool: claims success but writes nothing.
    (tmp_path / "tool.py").write_text("print('done')\n")
    r1 = _run_caps(["verify"], tmp_path)
    assert r1.returncode == 1, r1.stdout + r1.stderr
    assert "word-count-writes-output: fail" in r1.stdout

    # Fix the tool so the capability is actually true.
    (tmp_path / "tool.py").write_text("from pathlib import Path\nPath('out.txt').write_text('3\\n')\n")
    # The check runs the tool itself? No — make the check run the tool then assert.
    (tmp_path / "checks" / "test_output.py").write_text(textwrap.dedent("""
        import subprocess, sys
        from pathlib import Path
        def test_output():
            subprocess.run([sys.executable, "tool.py"], check=True)
            assert Path("out.txt").read_text().strip() == "3"
    """))
    r2 = _run_caps(["verify"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "word-count-writes-output: pass" in r2.stdout

    # Ledger recorded the final pass.
    import json
    ledger = json.loads((tmp_path / ".ctk" / "ledger.json").read_text())
    assert ledger["word-count-writes-output"]["result"] == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_caps_end_to_end.py -q`
Expected: It should actually PASS if Tasks 1–9 are complete (the system is built). If you are running this task in order before the CLI exists, it FAILS at the subprocess (`No module named caps`). Confirm the failure mode first if implementing strictly TDD, then proceed.

- [ ] **Step 3: No new implementation needed**

This task is a verification gate over the finished MVP. If the test fails for a real reason (not "module missing"), fix the offending module and its unit test, then re-run.

- [ ] **Step 4: Run the full suite**

Run: `./run_tests.sh`
Expected: all unit + integration tests PASS, including the existing ctk suite.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_caps_end_to_end.py
git commit -m "test(caps): end-to-end broken-then-fixed capability through the CLI"
```

---

## Manual smoke test (after Task 10)

Confirm the operator experience by hand from the repo root:

```bash
mkdir -p /tmp/capdemo/checks && cd /tmp/capdemo
cat > capabilities.yaml <<'YAML'
capabilities:
  - id: demo
    description: demo capability
    given: nothing
    when: a trivial check runs
    then: it passes
    tier: cheap
    deps: []
    check: { shell: "exit 0" }
YAML
PYTHONPATH=/Users/stuart.gano/Documents/claude-test-kit python -m caps status   # -> demo never proven
PYTHONPATH=/Users/stuart.gano/Documents/claude-test-kit python -m caps verify   # -> demo: pass, exit 0
PYTHONPATH=/Users/stuart.gano/Documents/claude-test-kit python -m caps status   # -> demo proven
```

---

## Self-Review

**Spec coverage (MVP scope only — hook/discovery are Phases 2/3):**
- Manifest with given/when/then, tier, deps, freshness, pytest|shell check → Task 2 ✓
- Tier-aware freshness defaults (cheap=code, live=24h) → Task 2 ✓
- Fingerprint over check + deps → Task 3 ✓
- Committed ledger with result states → Task 4 ✓
- Freshness (code + time) and waivers → Task 5 ✓
- Runner with pass/fail/error (incl. unreachable=error convention) → Task 6 ✓
- `verify` / `status` / `ack` CLI → Tasks 7–9 ✓
- Error: missing manifest, unknown capability, missing-deps warning → Tasks 2, 7, 8, 9 ✓
- End-to-end "claims success but didn't" caught → Task 10 ✓
- **Deferred (correctly, to later phases):** Stop hook enforcement, agent discovery, `SubagentStop`, the `error`-vs-`fail` nuance for pytest exceptions (MVP maps pytest non-0/1 to error; finer distinction is a Phase 2+ refinement).

**Placeholder scan:** No TBD/TODO/"add error handling"; every code step contains complete, runnable code.

**Type consistency:** `Capability` and `LedgerEntry` field names are defined once (Task 2 / Task 4) and used identically in Tasks 3, 5, 6, 7, 8, 9. CLI helpers `find_root`, `cmd_status`, `cmd_verify`, `cmd_ack` and constants `MANIFEST_NAME`, `LEDGER_REL`, `ERROR_EXIT` are consistent across Tasks 7–9.

---

## Next phases (separate plans)

- **Phase 2 — Enforcement:** the global Stop hook that calls this runner (fast/read-only by default, gates only on deps edited this session, blocks via exit 2 + reason, `stop_hook_active` guard). Re-verify the Stop hook payload (tool-calls availability) at the start of that plan.
- **Phase 3 — Discovery:** the `ctk` skill behavior that spots candidate capabilities from context, proposes them, and appends on approval (entering as never-proven).
