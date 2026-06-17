# Doc Staleness Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch all flavors of doc staleness — drift, dead links, orphans, explicit supersession (deterministic) and direction drift (LLM-first) — and prove it in band via two caps capabilities.

**Architecture:** A new deterministic ctk primitive `find_stale_docs` (mirrors `find_swallowed_exceptions`) backs a `cheap` `docs-current` capability. A separate LLM-first primitive `review_doc_direction` (shells out to `claude -p`, with its verdicts kept honest by evidence the kit verifies) backs a `live` `docs-direction-current` capability.

**Tech Stack:** Python 3, pytest, PyYAML (already vendored), Claude Code CLI (`claude`) for the direction review. No new Python dependencies.

## Global Constraints

- **No new Python dependencies.** Only the stdlib + PyYAML (already present). The direction review uses the `claude` CLI via subprocess, not an SDK.
- **No wall-clock in the deterministic layer.** `ctk/docs.py` must be a pure function of repo contents. Any age signal lives only in the LLM context of `ctk/docs_direction.py`, expressed as "days since last commit relative to repo HEAD."
- **No swallowed exceptions.** Per the kit's own ethos: an unreadable/malformed doc becomes a `Finding(severity="error")`, never a silent skip or bare `except: pass`. Do not log at ERROR inside library code (the autouse `fail_on_error_log` guard fails such tests).
- **`from __future__ import annotations`** at the top of every new module (matches existing ctk style).
- **Conventional commits** (`feat:`, `test:`, `docs:`), one per task.
- **Capabilities are wired with `caps add`, never by hand-editing `capabilities.yaml`.**
- Run tests with `PYTHONPATH=. .venv/bin/python -m pytest`.

## File Structure

- Create `ctk/docs.py` — `Finding`, `DocsConfig`, `find_stale_docs`, the five deterministic detectors, `format_findings`.
- Create `ctk/docs_direction.py` — `DirectionVerdict`, `ClaudeUnavailable`, `review_doc_direction`, the Claude-CLI runner seam, the evidence honesty check, `format_verdicts`.
- Modify `ctk/__init__.py` — export the new public symbols.
- Create `tests/unit/test_docs_unit.py` — unit tests for every deterministic detector.
- Create `tests/unit/test_docs_direction_unit.py` — unit tests for the direction review (stubbed runner; no real CLI).
- Create `tests/unit/test_docs_current.py` — the `docs-current` capability check.
- Create `checks/test_docs_direction.py` — the `docs-direction-current` capability check (scaffolded by `caps add`, then filled in).
- Modify `capabilities.yaml` — via `caps add` only (two new entries).

---

### Task 1: Module skeleton — `Finding`, `DocsConfig`, doc discovery, clean-pass

**Files:**
- Create: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Produces: `Finding(doc:str, line:int|None, kind:str, severity:str, message:str, evidence:str="")`; `DocsConfig` dataclass with defaults + `DocsConfig.from_yaml(path)->DocsConfig`; `find_stale_docs(doc_roots=..., repo_root=".", config:DocsConfig|None=None)->list[Finding]`; `format_findings(list[Finding])->str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_docs_unit.py
"""UNIT tests for ctk.docs — the deterministic doc-staleness detectors."""
import pytest

pytestmark = pytest.mark.unit

from ctk.docs import Finding, DocsConfig, find_stale_docs, format_findings


def test_clean_doc_has_no_findings(workspace):
    workspace.write("README.md", "# Title\n\nJust prose, no refs.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert findings == []


def test_finding_str_is_readable():
    f = Finding(doc="a.md", line=3, kind="broken_ref", severity="error",
                message="missing", evidence="x/y.py")
    s = str(f)
    assert "a.md:3" in s and "broken_ref" in s and "x/y.py" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ctk.docs'`

- [ ] **Step 3: Write minimal implementation**

```python
# ctk/docs.py
"""
Deterministic doc-staleness detection.

Mirrors ctk.lint.find_swallowed_exceptions: scan a set of docs and return a
list of Finding objects (empty == clean). Pure function of repo contents — no
wall-clock, no network — so it can back a `cheap` caps capability whose proof
is honest under fingerprint freshness.

Detectors: broken_ref, dead_link, orphan, superseded, assertion_failed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"


@dataclass
class Finding:
    doc: str
    line: Optional[int]
    kind: str        # broken_ref | dead_link | orphan | superseded | assertion_failed
    severity: str    # error | warn
    message: str
    evidence: str = ""

    def __str__(self) -> str:
        loc = f"{self.doc}:{self.line}" if self.line else self.doc
        ev = f"  ({self.evidence})" if self.evidence else ""
        return f"{loc}  [{self.kind}/{self.severity}]  {self.message}{ev}"


@dataclass
class DocsConfig:
    doc_roots: Sequence[str] = ("docs/", "README.md", "SKILL.md", "CLAUDE.md")
    entrypoints: Sequence[str] = ("README.md", "SKILL.md", "CLAUDE.md")
    ignore: Sequence[str] = ()                       # regexes: path-like tokens to skip
    orphan_exempt: Sequence[str] = ("docs/superpowers/",)
    known_top_dirs: Sequence[str] = ("caps/", "ctk/", "bin/", "tests/", "docs/", "examples/")
    tracked_ext: Sequence[str] = (
        ".py", ".md", ".sh", ".yaml", ".yml", ".txt", ".ini", ".toml", ".json",
    )
    severity_overrides: dict = field(default_factory=dict)   # kind -> severity
    direction: dict = field(default_factory=dict)            # consumed by docs_direction

    @classmethod
    def from_yaml(cls, path: str) -> "DocsConfig":
        import yaml
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        known = {f_.name for f_ in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _severity(kind: str, config: DocsConfig, default: str) -> str:
    return config.severity_overrides.get(kind, default)


def _iter_docs(doc_roots: Sequence[str], repo_root: str) -> list[str]:
    """Return repo-relative paths of all .md docs under the given roots."""
    out: list[str] = []
    for root in doc_roots:
        abs_root = os.path.join(repo_root, root)
        if os.path.isfile(abs_root):
            if abs_root.endswith(".md"):
                out.append(os.path.relpath(abs_root, repo_root))
        elif os.path.isdir(abs_root):
            for dirpath, dirs, files in os.walk(abs_root):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
                for fn in files:
                    if fn.endswith(".md"):
                        p = os.path.join(dirpath, fn)
                        out.append(os.path.relpath(p, repo_root))
    return sorted(set(out))


def find_stale_docs(
    doc_roots: Sequence[str] = ("docs/", "README.md", "SKILL.md", "CLAUDE.md"),
    repo_root: str = ".",
    config: Optional[DocsConfig] = None,
) -> list[Finding]:
    config = config or DocsConfig(doc_roots=doc_roots)
    findings: list[Finding] = []
    docs = _iter_docs(doc_roots, repo_root)
    # detectors are added in later tasks
    return findings


def format_findings(findings: Sequence[Finding]) -> str:
    if not findings:
        return "no findings"
    return "\n".join("  " + str(f) for f in findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs skeleton — Finding, DocsConfig, doc discovery"
```

---

### Task 2: `broken_ref` detector (drift — error)

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: `find_stale_docs`, `Finding`, `DocsConfig` from Task 1.
- Produces: a `broken_ref` finding for inline `` `code spans` `` that strongly resemble a repo path but don't resolve; illustrative placeholders and `config.ignore` patterns are skipped.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_broken_ref_flags_missing_code_span_path(workspace):
    workspace.write("README.md", "See `caps/nope.py` for details.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    kinds = [(f.kind, f.severity) for f in findings]
    assert ("broken_ref", "error") in kinds


def test_broken_ref_clean_when_path_exists(workspace):
    workspace.write("caps/real.py", "x = 1\n")
    workspace.write("README.md", "See `caps/real.py`.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "broken_ref"] == []


def test_broken_ref_ignores_illustrative_paths(workspace):
    workspace.write("README.md", "e.g. `path/to/file.py` or `<your-kit>/x.py`.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "broken_ref"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k broken_ref -v`
Expected: FAIL (`test_broken_ref_flags_missing_code_span_path` — no findings returned)

- [ ] **Step 3: Write minimal implementation**

Add near the top of `ctk/docs.py` (after imports):

```python
import re

_CODE_SPAN = re.compile(r"`([^`]+)`")
_PLACEHOLDER = re.compile(r"(path/to/|/\.\.\.|<[^>]+>|\bexample/|\bfoo/|\bbar/|\$\{)")


def _looks_like_repo_path(tok: str, config: DocsConfig) -> bool:
    tok = tok.strip()
    if not tok or tok.startswith(("http://", "https://", "#", "mailto:", "/")):
        return False
    if _PLACEHOLDER.search(tok):
        return False
    if any(re.search(p, tok) for p in config.ignore):
        return False
    if tok.startswith(tuple(config.known_top_dirs)):
        return True
    if "/" in tok and tok.endswith(tuple(config.tracked_ext)):
        return True
    return False


def _exists(rel_path: str, repo_root: str) -> bool:
    rel_path = rel_path.split("#", 1)[0].strip()
    return bool(rel_path) and os.path.exists(os.path.join(repo_root, rel_path))


def _detect_broken_refs(doc: str, text: str, repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _CODE_SPAN.finditer(line):
            tok = m.group(1)
            if _looks_like_repo_path(tok, config) and not _exists(tok, repo_root):
                out.append(Finding(doc, i, "broken_ref",
                                   _severity("broken_ref", config, SEVERITY_ERROR),
                                   "code-span path does not exist", tok))
    return out
```

Then add a doc-reading loop body to `find_stale_docs` (replace the `# detectors are added in later tasks` line):

```python
    for doc in docs:
        try:
            with open(os.path.join(repo_root, doc), "r", errors="strict") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            findings.append(Finding(doc, None, "broken_ref",
                                    SEVERITY_ERROR, f"could not read doc: {e}", doc))
            continue
        findings.extend(_detect_broken_refs(doc, text, repo_root, config))
```

(The unreadable-doc branch is exercised properly in Task 7; this is its first home.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k broken_ref -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs broken_ref detector"
```

---

### Task 3: `dead_link` detector (internal links — error; missing anchor — warn)

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: Task 1–2 symbols, `_exists`, `_severity`.
- Produces: a `dead_link` error for markdown link targets `[..](target)` that are relative repo paths whose file is missing; a `dead_link` warn for an existing target whose `#anchor` is absent.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_dead_link_flags_missing_target(workspace):
    workspace.write("README.md", "See [the guide](docs/missing.md).\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert ("dead_link", "error") in [(f.kind, f.severity) for f in findings]


def test_dead_link_clean_for_existing_target(workspace):
    workspace.write("docs/guide.md", "# Guide\n")
    workspace.write("README.md", "See [guide](docs/guide.md).\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "dead_link"] == []


def test_dead_link_warns_on_missing_anchor(workspace):
    workspace.write("docs/guide.md", "# Guide\n\n## Setup\n")
    workspace.write("README.md", "See [setup](docs/guide.md#nonexistent).\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert ("dead_link", "warn") in [(f.kind, f.severity) for f in findings]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k dead_link -v`
Expected: FAIL (no `dead_link` findings)

- [ ] **Step 3: Write minimal implementation**

Add to `ctk/docs.py`:

```python
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _slugify_heading(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s)


def _anchors_in(path: str, repo_root: str) -> set[str]:
    out: set[str] = set()
    try:
        with open(os.path.join(repo_root, path), "r", errors="replace") as f:
            for line in f:
                m = re.match(r"#{1,6}\s+(.*)", line)
                if m:
                    out.add(_slugify_heading(m.group(1)))
    except OSError:
        pass
    return out


def _is_relative_repo_target(target: str) -> bool:
    target = target.strip()
    return bool(target) and not target.startswith(
        ("http://", "https://", "mailto:", "#", "/"))


def _detect_dead_links(doc: str, text: str, repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    doc_dir = os.path.dirname(doc)
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _MD_LINK.finditer(line):
            target = m.group(1).strip()
            if not _is_relative_repo_target(target):
                continue
            file_part, _, anchor = target.partition("#")
            rel = os.path.normpath(os.path.join(doc_dir, file_part)) if file_part else doc
            if not os.path.exists(os.path.join(repo_root, rel)):
                out.append(Finding(doc, i, "dead_link",
                                   _severity("dead_link", config, SEVERITY_ERROR),
                                   "link target does not exist", target))
            elif anchor and _slugify_heading(anchor) not in _anchors_in(rel, repo_root):
                out.append(Finding(doc, i, "dead_link",
                                   _severity("dead_link_anchor", config, SEVERITY_WARN),
                                   "link anchor not found in target", target))
    return out
```

Add to the per-doc loop in `find_stale_docs`, after the `broken_ref` line:

```python
        findings.extend(_detect_dead_links(doc, text, repo_root, config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k dead_link -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs dead_link detector (file + anchor)"
```

---

### Task 4: `orphan` detector (reachability — warn)

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: Task 1–3 symbols.
- Produces: an `orphan` warn for any doc unreachable from `config.entrypoints` via the internal markdown-link graph; entrypoints and `config.orphan_exempt`-prefixed docs are never orphans.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_orphan_flags_unlinked_doc(workspace):
    workspace.write("README.md", "Start. [guide](docs/guide.md)\n")
    workspace.write("docs/guide.md", "# Guide\n")
    workspace.write("docs/lonely.md", "# Nobody links here\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    orphans = [f.doc for f in findings if f.kind == "orphan"]
    assert "docs/lonely.md" in orphans
    assert "docs/guide.md" not in orphans
    assert "README.md" not in orphans


def test_orphan_exempts_archival_tree(workspace):
    workspace.write("README.md", "# Root\n")
    workspace.write("docs/superpowers/specs/old.md", "# archived\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "orphan"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k orphan -v`
Expected: FAIL (no `orphan` findings)

- [ ] **Step 3: Write minimal implementation**

Add to `ctk/docs.py`:

```python
def _outgoing_doc_links(doc: str, text: str, repo_root: str) -> list[str]:
    doc_dir = os.path.dirname(doc)
    out: list[str] = []
    for m in _MD_LINK.finditer(text):
        target = m.group(1).strip()
        if not _is_relative_repo_target(target):
            continue
        file_part = target.partition("#")[0]
        if not file_part.endswith(".md"):
            continue
        rel = os.path.normpath(os.path.join(doc_dir, file_part))
        if os.path.exists(os.path.join(repo_root, rel)):
            out.append(rel)
    return out


def _detect_orphans(docs: list[str], texts: dict[str, str],
                    repo_root: str, config: DocsConfig) -> list[Finding]:
    reachable: set[str] = set()
    frontier = [os.path.normpath(e) for e in config.entrypoints
                if os.path.exists(os.path.join(repo_root, e))]
    reachable.update(frontier)
    while frontier:
        cur = frontier.pop()
        for nxt in _outgoing_doc_links(cur, texts.get(cur, ""), repo_root):
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    out: list[Finding] = []
    exempt = tuple(config.orphan_exempt)
    entry = {os.path.normpath(e) for e in config.entrypoints}
    for doc in docs:
        if doc in reachable or doc in entry or doc.startswith(exempt):
            continue
        out.append(Finding(doc, None, "orphan",
                           _severity("orphan", config, SEVERITY_WARN),
                           "doc is not reachable from any entrypoint", doc))
    return out
```

Refactor `find_stale_docs` to read every doc once into a `texts` dict, run per-doc detectors, then the cross-doc orphan pass. Replace the per-doc loop body with:

```python
    texts: dict[str, str] = {}
    for doc in docs:
        try:
            with open(os.path.join(repo_root, doc), "r", errors="strict") as f:
                texts[doc] = f.read()
        except (OSError, UnicodeDecodeError) as e:
            findings.append(Finding(doc, None, "broken_ref",
                                    SEVERITY_ERROR, f"could not read doc: {e}", doc))
    for doc, text in texts.items():
        findings.extend(_detect_broken_refs(doc, text, repo_root, config))
        findings.extend(_detect_dead_links(doc, text, repo_root, config))
    findings.extend(_detect_orphans(list(texts), texts, repo_root, config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k orphan -v`
Expected: PASS (2 passed); also rerun the whole file to confirm no regressions:
`PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs orphan detector (link-graph reachability)"
```

---

### Task 5: `superseded` detector (explicit retirement — warn)

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: Task 1–4 symbols.
- Produces: `_front_matter(text)->(dict, int)` (parsed YAML front-matter + number of header lines consumed); a `superseded` warn when front-matter has `superseded_by:`, OR prose says "superseded/replaced/deprecated by [..](target)" with a resolving target, OR a newer doc shares this doc's spec slug.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_superseded_via_front_matter(workspace):
    workspace.write("docs/a.md", "---\nsuperseded_by: docs/b.md\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert ("superseded", "warn") in [(f.kind, f.severity) for f in findings]


def test_superseded_via_prose(workspace):
    workspace.write("docs/b.md", "# B\n")
    workspace.write("docs/a.md", "# A\n\nThis is superseded by [B](b.md).\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any(f.kind == "superseded" and f.doc == "docs/a.md" for f in findings)


def test_superseded_via_newer_same_slug(workspace):
    workspace.write("docs/specs/2026-01-01-thing-design.md", "# old\n")
    workspace.write("docs/specs/2026-02-01-thing-design.md", "# new\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    superseded = [f.doc for f in findings if f.kind == "superseded"]
    assert "docs/specs/2026-01-01-thing-design.md" in superseded
    assert "docs/specs/2026-02-01-thing-design.md" not in superseded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k superseded -v`
Expected: FAIL (no `superseded` findings)

- [ ] **Step 3: Write minimal implementation**

Add to `ctk/docs.py`:

```python
_SPEC_SLUG = re.compile(r"(\d{4}-\d{2}-\d{2})-(.+?)(?:-design|-discovery)?\.md$")
_SUPERSEDE_PROSE = re.compile(
    r"(superseded|replaced|deprecated)\s+by\s+\[[^\]]*\]\(([^)]+)\)", re.IGNORECASE)


def _front_matter(text: str) -> tuple[dict, int]:
    """Return (front_matter_dict, lines_consumed). Empty dict if none.

    Raises ValueError on malformed YAML so the caller can record an error
    Finding rather than swallowing it.
    """
    if not text.startswith("---\n"):
        return {}, 0
    end = text.find("\n---", 4)
    if end == -1:
        return {}, 0
    block = text[4:end]
    import yaml
    data = yaml.safe_load(block)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("front matter is not a mapping")
    return data, block.count("\n") + 2


def _detect_superseded(docs: list[str], texts: dict[str, str],
                       repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    sev = _severity("superseded", config, SEVERITY_WARN)
    # newest date per spec slug
    latest: dict[str, str] = {}
    for doc in docs:
        m = _SPEC_SLUG.search(os.path.basename(doc))
        if m:
            date, slug = m.group(1), m.group(2)
            if slug not in latest or date > latest[slug]:
                latest[slug] = date
    for doc, text in texts.items():
        fm, _ = _front_matter(text)
        if "superseded_by" in fm:
            out.append(Finding(doc, 1, "superseded", sev,
                               "front-matter declares superseded_by",
                               str(fm["superseded_by"])))
            continue
        pm = _SUPERSEDE_PROSE.search(text)
        if pm:
            doc_dir = os.path.dirname(doc)
            tgt = os.path.normpath(os.path.join(doc_dir, pm.group(2).partition("#")[0]))
            if os.path.exists(os.path.join(repo_root, tgt)):
                out.append(Finding(doc, None, "superseded", sev,
                                   "prose says superseded/replaced/deprecated by",
                                   pm.group(2)))
                continue
        m = _SPEC_SLUG.search(os.path.basename(doc))
        if m and m.group(1) < latest.get(m.group(2), m.group(1)):
            out.append(Finding(doc, None, "superseded", sev,
                               "a newer doc shares this spec slug",
                               f"newer: {latest[m.group(2)]}"))
    return out
```

Add to `find_stale_docs` after the orphan line:

```python
    findings.extend(_detect_superseded(list(texts), texts, repo_root, config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k superseded -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs superseded detector (front-matter/prose/newer-slug)"
```

---

### Task 6: `assertion_failed` detector (front-matter `ctk:` claims — error)

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: Task 1–5 symbols, `_front_matter`.
- Produces: an `assertion_failed` error for each unmet `ctk.requires_paths` entry (path absent) and each unmet `ctk.requires_grep` entry (`{file, pattern}` — file missing or regex not found).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_assertion_requires_paths_fails_when_missing(workspace):
    workspace.write("docs/a.md", "---\nctk:\n  requires_paths: [caps/gone.py]\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert ("assertion_failed", "error") in [(f.kind, f.severity) for f in findings]


def test_assertion_requires_grep_pass_and_fail(workspace):
    workspace.write("caps/freshness.py", "WINDOW_HOURS = 24\n")
    workspace.write("docs/ok.md",
        "---\nctk:\n  requires_grep:\n    - {file: caps/freshness.py, pattern: '24'}\n---\n# ok\n")
    workspace.write("docs/bad.md",
        "---\nctk:\n  requires_grep:\n    - {file: caps/freshness.py, pattern: '99'}\n---\n# bad\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    bad = [f for f in findings if f.kind == "assertion_failed"]
    assert len(bad) == 1 and bad[0].doc == "docs/bad.md"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k assertion -v`
Expected: FAIL (no `assertion_failed` findings)

- [ ] **Step 3: Write minimal implementation**

Add to `ctk/docs.py`:

```python
def _detect_assertions(doc: str, text: str, repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    sev = _severity("assertion_failed", config, SEVERITY_ERROR)
    fm, _ = _front_matter(text)
    ctk_block = fm.get("ctk") or {}
    if not isinstance(ctk_block, dict):
        return out
    for p in ctk_block.get("requires_paths", []) or []:
        if not os.path.exists(os.path.join(repo_root, str(p))):
            out.append(Finding(doc, 1, "assertion_failed", sev,
                               "requires_paths target does not exist", str(p)))
    for entry in ctk_block.get("requires_grep", []) or []:
        f_path = str(entry.get("file", ""))
        pattern = str(entry.get("pattern", ""))
        abs_p = os.path.join(repo_root, f_path)
        if not os.path.exists(abs_p):
            out.append(Finding(doc, 1, "assertion_failed", sev,
                               "requires_grep file does not exist", f_path))
            continue
        with open(abs_p, "r", errors="replace") as fh:
            if not re.search(pattern, fh.read()):
                out.append(Finding(doc, 1, "assertion_failed", sev,
                                   f"requires_grep pattern not found: {pattern}", f_path))
    return out
```

Add to the per-doc detector loop in `find_stale_docs` (alongside broken_ref/dead_link):

```python
        findings.extend(_detect_assertions(doc, text, repo_root, config))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k assertion -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs assertion_failed detector (requires_paths/requires_grep)"
```

---

### Task 7: Error handling — unreadable doc & malformed front-matter become findings

**Files:**
- Modify: `ctk/docs.py`
- Test: `tests/unit/test_docs_unit.py`

**Interfaces:**
- Consumes: Task 1–6 symbols.
- Produces: malformed front-matter yields an `assertion_failed` error finding (not a crash); the existing unreadable-doc branch is covered by a test.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_unit.py
def test_malformed_front_matter_is_a_finding_not_a_crash(workspace):
    # ': :' is invalid YAML inside the front-matter block
    workspace.write("docs/a.md", "---\n: : :\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any(f.severity == "error" and "front matter" in f.message.lower()
               for f in findings)


def test_unreadable_doc_is_a_finding(workspace, monkeypatch):
    workspace.write("docs/a.md", "# A\n")
    import ctk.docs as d
    real_open = open

    def boom(path, *a, **k):
        if str(path).endswith("docs/a.md"):
            raise OSError("permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", boom)
    findings = d.find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any("could not read" in f.message for f in findings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -k "malformed or unreadable" -v`
Expected: FAIL on `test_malformed_front_matter_is_a_finding_not_a_crash` (currently raises `ValueError` out of `_front_matter`).

- [ ] **Step 3: Write minimal implementation**

Wrap the front-matter-consuming detectors so a `ValueError` becomes a finding. In `find_stale_docs`, replace the per-doc detector loop with a version that guards front-matter parsing once per doc:

```python
    for doc, text in texts.items():
        findings.extend(_detect_broken_refs(doc, text, repo_root, config))
        findings.extend(_detect_dead_links(doc, text, repo_root, config))
        try:
            findings.extend(_detect_assertions(doc, text, repo_root, config))
        except ValueError as e:
            findings.append(Finding(doc, 1, "assertion_failed", SEVERITY_ERROR,
                                    f"malformed front matter: {e}", doc))
```

And guard the superseded pass's front-matter read the same way by making `_detect_superseded` skip (not crash) a doc whose front-matter is malformed — change its `fm, _ = _front_matter(text)` to:

```python
        try:
            fm, _ = _front_matter(text)
        except ValueError:
            fm = {}   # malformed front matter is already reported by _detect_assertions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_unit.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs.py tests/unit/test_docs_unit.py
git commit -m "feat: ctk.docs treats unreadable/malformed docs as findings, not crashes"
```

---

### Task 8: Export, wire the `docs-current` capability, dogfood on this repo

**Files:**
- Modify: `ctk/__init__.py`
- Create: `tests/unit/test_docs_current.py`
- Modify: `capabilities.yaml` (via `caps add` only)

**Interfaces:**
- Consumes: `find_stale_docs`, `format_findings` from `ctk.docs`.
- Produces: public exports `find_stale_docs`, `Finding`, `DocsConfig`, `format_findings`; a green `docs-current` capability.

- [ ] **Step 1: Add the exports**

In `ctk/__init__.py`, add after the `from .lint import ...` line:

```python
from .docs import find_stale_docs, Finding, DocsConfig, format_findings
```

and add `"find_stale_docs"`, `"Finding"`, `"DocsConfig"`, `"format_findings"` to `__all__`.

- [ ] **Step 2: Write the capability check**

```python
# tests/unit/test_docs_current.py
"""Capability check for `docs-current`: docs don't drift from reality."""
import pytest

pytestmark = pytest.mark.unit

from ctk import find_stale_docs, format_findings


def test_no_stale_docs():
    errors = [f for f in find_stale_docs() if f.severity == "error"]
    assert errors == [], "\n" + format_findings(errors)
```

- [ ] **Step 3: Run it to see the repo's real state**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_current.py -v`
Expected: may FAIL, listing real `broken_ref`/`dead_link`/`assertion_failed` errors in this repo's docs. Fix each by correcting the doc (update the path/link, or add `config.ignore`/front-matter as appropriate). Re-run until PASS. Do not weaken the detector to make it pass — fix the docs.

- [ ] **Step 4: Wire the capability with `caps add`**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m caps add \
  --id docs-current --tier cheap \
  --description "project docs don't drift from reality (no broken refs, dead links, or failed doc assertions)" \
  --given "the docs tree and the code it describes" \
  --when "the docs staleness scan runs" \
  --then "no error-severity findings" \
  --deps "docs/**" --deps "README.md" --deps "SKILL.md" --deps "CLAUDE.md" \
  --deps "caps/**" --deps "ctk/**" --deps "bin/**" --deps "docs-policy.yaml" \
  --check tests/unit/test_docs_current.py::test_no_stale_docs
```

Note: `caps add` scaffolds a failing stub only when the check file doesn't exist; since `tests/unit/test_docs_current.py` already exists (Step 2), it appends the manifest entry and leaves the real check in place. If `caps add` reports it scaffolded over the file, restore the Step-2 content.

- [ ] **Step 5: Prove it green and commit**

Run: `PYTHONPATH=. .venv/bin/python -m caps verify --capability docs-current`
Expected: the capability verifies (proof recorded in `.ctk/ledger.json`).

```bash
git add ctk/__init__.py tests/unit/test_docs_current.py capabilities.yaml .ctk/ledger.json
# include any docs you fixed in Step 3
git commit -m "feat: docs-current capability — prove docs don't drift (dogfooded)"
```

---

### Task 9: `review_doc_direction` — Claude-CLI direction review with a stubbable runner

**Files:**
- Create: `ctk/docs_direction.py`
- Test: `tests/unit/test_docs_direction_unit.py`

**Interfaces:**
- Produces: `DirectionVerdict(doc, verdict, rationale, doc_evidence:list[str], source_evidence:list[str])`; `ClaudeUnavailable(Exception)`; `review_doc_direction(docs, repo_root=".", config=None, runner=None)->list[DirectionVerdict]`; `format_verdicts(list[DirectionVerdict])->str`. `runner` is `Callable[[str], str]` returning raw model stdout — the seam tests inject.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_docs_direction_unit.py
"""UNIT tests for ctk.docs_direction — direction review with a stubbed runner."""
import json
import pytest

pytestmark = pytest.mark.unit

from ctk.docs_direction import (
    review_doc_direction, DirectionVerdict, format_verdicts,
)


def _runner_returning(payload):
    return lambda prompt: json.dumps(payload)


def test_current_verdict_parsed(workspace):
    workspace.write("README.md", "# Project\nCurrent truth.\n")
    workspace.write("docs/a.md", "# A\nStill accurate.\n")
    runner = _runner_returning({"verdict": "current", "rationale": "matches",
                                "doc_evidence": [], "source_evidence": []})
    verdicts = review_doc_direction(
        ["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert len(verdicts) == 1 and verdicts[0].verdict == "current"


def test_format_verdicts_lists_docs():
    v = DirectionVerdict("docs/a.md", "overtaken", "pivoted", ["x"], ["y"])
    assert "docs/a.md" in format_verdicts([v])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ctk.docs_direction'`

- [ ] **Step 3: Write minimal implementation**

```python
# ctk/docs_direction.py
"""
LLM-first doc *direction* review.

Component 1 (ctk.docs) catches mechanical drift deterministically. This module
makes the judgment a regex can't: has the project's content and direction moved
PAST this doc, regardless of age? It shells out to the `claude` CLI, then keeps
the verdict honest — an `overtaken` verdict is only trusted if the exact lines
it quotes really appear in the named files (see ctk.docs_direction.verify_*).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

VALID_VERDICTS = ("current", "overtaken", "uncertain")


class ClaudeUnavailable(Exception):
    """Raised when the `claude` CLI is not available to run the review."""


@dataclass
class DirectionVerdict:
    doc: str
    verdict: str                       # current | overtaken | uncertain
    rationale: str
    doc_evidence: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.doc}  [{self.verdict}]  {self.rationale}"


def _authoritative_context(repo_root: str) -> str:
    parts: list[str] = []
    for name in ("README.md", "SKILL.md", "CLAUDE.md", "capabilities.yaml"):
        p = os.path.join(repo_root, name)
        if os.path.exists(p):
            with open(p, "r", errors="replace") as f:
                parts.append(f"### {name}\n{f.read()}")
    return "\n\n".join(parts)


def _build_prompt(doc: str, doc_text: str, context: str) -> str:
    return (
        "You are auditing whether a project doc still matches the project's "
        "current direction. Consider supersession, work described as future "
        "that is now shipped, and decisions the current sources contradict. "
        "Do NOT judge on age alone.\n\n"
        "Reply with ONLY a JSON object: "
        '{"verdict": "current|overtaken|uncertain", "rationale": "...", '
        '"doc_evidence": ["exact quoted line from the doc"], '
        '"source_evidence": ["exact quoted line from a current source"]}. '
        "For 'overtaken', doc_evidence and source_evidence MUST be exact "
        "substrings copied verbatim from the texts below.\n\n"
        f"=== DOC UNDER REVIEW: {doc} ===\n{doc_text}\n\n"
        f"=== CURRENT AUTHORITATIVE SOURCES ===\n{context}\n"
    )


def _claude_cli_runner(prompt: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeUnavailable("claude CLI not on PATH")
    proc = subprocess.run(
        [exe, "-p", prompt], capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise ClaudeUnavailable(f"claude exited {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout


def _parse_verdict(doc: str, raw: str) -> DirectionVerdict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return DirectionVerdict(doc, "uncertain", "no JSON in model output")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return DirectionVerdict(doc, "uncertain", f"unparseable verdict: {e}")
    verdict = data.get("verdict", "uncertain")
    if verdict not in VALID_VERDICTS:
        verdict = "uncertain"
    return DirectionVerdict(
        doc=doc,
        verdict=verdict,
        rationale=str(data.get("rationale", "")),
        doc_evidence=[str(x) for x in data.get("doc_evidence", []) or []],
        source_evidence=[str(x) for x in data.get("source_evidence", []) or []],
    )


def review_doc_direction(
    docs: Sequence[str],
    repo_root: str = ".",
    config=None,
    runner: Optional[Callable[[str], str]] = None,
) -> list[DirectionVerdict]:
    runner = runner or _claude_cli_runner
    context = _authoritative_context(repo_root)
    verdicts: list[DirectionVerdict] = []
    for doc in docs:
        with open(os.path.join(repo_root, doc), "r", errors="replace") as f:
            doc_text = f.read()
        raw = runner(_build_prompt(doc, doc_text, context))
        verdicts.append(_parse_verdict(doc, raw))
    return verdicts


def format_verdicts(verdicts: Sequence[DirectionVerdict]) -> str:
    return "\n".join("  " + str(v) for v in verdicts) or "no verdicts"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs_direction.py tests/unit/test_docs_direction_unit.py
git commit -m "feat: ctk.docs_direction — LLM direction review via claude CLI (stubbable)"
```

---

### Task 10: Honesty wrapper — discard `overtaken` verdicts whose evidence isn't real

**Files:**
- Modify: `ctk/docs_direction.py`
- Test: `tests/unit/test_docs_direction_unit.py`

**Interfaces:**
- Consumes: Task 9 symbols.
- Produces: inside `review_doc_direction`, any `overtaken` verdict is downgraded to `uncertain` (rationale annotated) unless every `doc_evidence` quote appears in the reviewed doc AND every `source_evidence` quote appears in some authoritative source. This is the `claim_vs_reality` principle applied to the LLM judge.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_direction_unit.py
def test_overtaken_with_real_evidence_is_kept(workspace):
    workspace.write("README.md", "# Project\nThe default window is 48h now.\n")
    workspace.write("docs/a.md", "# A\nThe default window is 24h.\n")
    runner = _runner_returning({
        "verdict": "overtaken", "rationale": "window changed",
        "doc_evidence": ["The default window is 24h."],
        "source_evidence": ["The default window is 48h now."]})
    verdicts = review_doc_direction(
        ["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert verdicts[0].verdict == "overtaken"


def test_overtaken_with_fake_evidence_is_discarded(workspace):
    workspace.write("README.md", "# Project\nNothing relevant here.\n")
    workspace.write("docs/a.md", "# A\nReal content.\n")
    runner = _runner_returning({
        "verdict": "overtaken", "rationale": "hallucinated",
        "doc_evidence": ["a line that is not in the doc"],
        "source_evidence": ["also not present anywhere"]})
    verdicts = review_doc_direction(
        ["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert verdicts[0].verdict == "uncertain"
    assert "evidence" in verdicts[0].rationale.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -k overtaken -v`
Expected: FAIL on `test_overtaken_with_fake_evidence_is_discarded` (verdict still "overtaken").

- [ ] **Step 3: Write minimal implementation**

Add to `ctk/docs_direction.py`:

```python
def _quote_present(quote: str, haystacks: Sequence[str]) -> bool:
    q = " ".join(quote.split())
    return any(q and " ".join(h.split()).find(q) != -1 for h in haystacks)


def _verify_evidence(v: DirectionVerdict, doc_text: str, context: str) -> DirectionVerdict:
    if v.verdict != "overtaken":
        return v
    doc_ok = v.doc_evidence and all(_quote_present(q, [doc_text]) for q in v.doc_evidence)
    src_ok = v.source_evidence and all(_quote_present(q, [context]) for q in v.source_evidence)
    if doc_ok and src_ok:
        return v
    return DirectionVerdict(
        v.doc, "uncertain",
        f"overtaken claim discarded — evidence not verifiable ({v.rationale})",
        v.doc_evidence, v.source_evidence)
```

Then in `review_doc_direction`, change the append line to verify first:

```python
        raw = runner(_build_prompt(doc, doc_text, context))
        verdict = _parse_verdict(doc, raw)
        verdicts.append(_verify_evidence(verdict, doc_text, context))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add ctk/docs_direction.py tests/unit/test_docs_direction_unit.py
git commit -m "feat: ctk.docs_direction honesty wrapper — verify LLM evidence or discard"
```

---

### Task 11: Graceful degradation when the `claude` CLI is absent

**Files:**
- Modify: `ctk/docs_direction.py` (only if needed)
- Test: `tests/unit/test_docs_direction_unit.py`

**Interfaces:**
- Consumes: Task 9–10 symbols.
- Produces: the default runner raises `ClaudeUnavailable` when `claude` is not on PATH (so the capability check can `pytest.skip` — fail-open). Verified directly.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_docs_direction_unit.py
def test_default_runner_raises_when_cli_missing(monkeypatch):
    import ctk.docs_direction as dd
    monkeypatch.setattr(dd.shutil, "which", lambda _name: None)
    with pytest.raises(dd.ClaudeUnavailable):
        dd._claude_cli_runner("any prompt")
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -k cli_missing -v`
Expected: PASS already (the `shutil.which` guard was written in Task 9). If it fails, ensure `_claude_cli_runner` raises `ClaudeUnavailable` before calling `subprocess.run`.

- [ ] **Step 3: Implementation**

No new code expected — this task pins the contract. If Step 2 failed, the fix is the guard already shown in Task 9 Step 3 (`if not exe: raise ClaudeUnavailable(...)`).

- [ ] **Step 4: Re-run to confirm**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_direction_unit.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_docs_direction_unit.py
git commit -m "test: pin claude-CLI-absent degradation contract"
```

---

### Task 12: Export, wire the `docs-direction-current` capability

**Files:**
- Modify: `ctk/__init__.py`
- Create: `checks/test_docs_direction.py`
- Modify: `capabilities.yaml` (via `caps add` only)

**Interfaces:**
- Consumes: `review_doc_direction`, `format_verdicts`, `ClaudeUnavailable` from `ctk.docs_direction`.
- Produces: public exports for the direction symbols; a `docs-direction-current` live capability whose check runs the review (or skips, fail-open, if `claude` is unavailable).

- [ ] **Step 1: Add the exports**

In `ctk/__init__.py`, after the `from .docs import ...` line add:

```python
from .docs_direction import (
    review_doc_direction, DirectionVerdict, ClaudeUnavailable, format_verdicts,
)
```

and add `"review_doc_direction"`, `"DirectionVerdict"`, `"ClaudeUnavailable"`, `"format_verdicts"` to `__all__`.

- [ ] **Step 2: Write the capability check**

```python
# checks/test_docs_direction.py
"""Capability check for `docs-direction-current` (live, LLM-first).

Runs the evidence-verified direction review over the project's docs. Fail-open:
if the `claude` CLI is unavailable, the check skips (the live capability is left
un-proven rather than falsely proven — re-prove when the CLI is present, or
`caps ack` with a reason).
"""
import glob
import pytest

from ctk import (
    review_doc_direction, ClaudeUnavailable, format_verdicts,
)


def _select_direction_docs():
    # Review narrative docs; skip the archival spec/plan tree by default.
    docs = ["README.md", "SKILL.md", "CLAUDE.md"]
    docs += [d for d in glob.glob("docs/**/*.md", recursive=True)
             if not d.startswith("docs/superpowers/")]
    return [d for d in docs if glob.os.path.exists(d)]


def test_no_overtaken_docs():
    try:
        verdicts = review_doc_direction(_select_direction_docs())
    except ClaudeUnavailable as e:
        pytest.skip(f"claude CLI unavailable — direction review skipped: {e}")
    overtaken = [v for v in verdicts if v.verdict == "overtaken"]
    assert overtaken == [], "\n" + format_verdicts(overtaken)
```

- [ ] **Step 3: Wire the capability with `caps add`**

```bash
PYTHONPATH=. .venv/bin/python -m caps add \
  --id docs-direction-current --tier live \
  --description "docs still match the project's current direction (LLM review, evidence-verified)" \
  --given "the docs and the current authoritative sources" \
  --when "the LLM direction review runs and its evidence is verified" \
  --then "no doc is left in an un-acknowledged overtaken state" \
  --deps "docs/**" --deps "README.md" --deps "SKILL.md" --deps "CLAUDE.md" \
  --deps "capabilities.yaml" \
  --check checks/test_docs_direction.py::test_no_overtaken_docs
```

If `caps add` scaffolds a stub because `checks/test_docs_direction.py` didn't exist yet, replace the scaffolded content with the Step-2 check.

- [ ] **Step 4: Verify (skips or proves depending on CLI presence)**

Run: `PYTHONPATH=. .venv/bin/python -m caps verify --capability docs-direction-current`
Expected: if `claude` is on PATH, the review runs and the capability proves (fix any genuinely overtaken docs first by retiring them or `caps ack`); if not, the check skips and the capability stays un-proven (fail-open, as designed).

- [ ] **Step 5: Commit**

```bash
git add ctk/__init__.py checks/test_docs_direction.py capabilities.yaml .ctk/ledger.json
git commit -m "feat: docs-direction-current capability — LLM-first, evidence-verified"
```

---

### Task 13: Full-suite green + capability sweep + spec/README touch-up

**Files:**
- Modify: `README.md` (document the two new primitives + capabilities)
- Possibly modify: any repo docs flagged by `docs-current`

**Interfaces:**
- Consumes: everything above.
- Produces: a fully green suite, both capabilities accounted for, and user-facing docs that describe the feature (which `docs-current` itself will then keep honest).

- [ ] **Step 1: Run the whole suite**

Run: `./run_tests.sh`
Expected: all tests pass. Fix any regressions before proceeding.

- [ ] **Step 2: Capability sweep**

Run: `PYTHONPATH=. .venv/bin/python -m caps status`
Expected: `docs-current` is proven/green; `docs-direction-current` is proven or cleanly skipped (never falsely green). Resolve anything red.

- [ ] **Step 3: Document the feature in README**

Add a short subsection under Layer 1 describing `find_stale_docs` (the five detectors, severity model) and under Layer 2 describing the two capabilities (`docs-current` cheap, `docs-direction-current` live + the evidence-honesty wrapper). Use real symbol names and the actual check paths so `docs-current` stays satisfied.

- [ ] **Step 4: Re-prove docs-current after the README edit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_docs_current.py -v && PYTHONPATH=. .venv/bin/python -m caps verify --capability docs-current`
Expected: PASS / proven (the README you just wrote contains no broken refs or dead links).

- [ ] **Step 5: Commit**

```bash
git add README.md capabilities.yaml .ctk/ledger.json
# include any docs fixed in this task
git commit -m "docs: document doc-staleness detection (ctk.docs + caps) and re-prove"
```

---

## Self-Review

**Spec coverage:**
- Component 1 (deterministic detectors) → Tasks 1–7 (one per detector + error handling). ✓
- Component 2 (`DocsConfig`, `from_yaml`, severity overrides, `orphan_exempt`) → Task 1 (config) + used throughout. ✓
- Component 3 (LLM-first direction, Claude CLI, evidence honesty, graceful degradation) → Tasks 9–11. ✓
- Component 4 (`docs-current` cheap + `docs-direction-current` live capabilities) → Tasks 8 and 12. ✓
- Determinism (no wall-clock in deterministic layer; age only in LLM input) → Global Constraints + Task 9 prompt. ✓
- No-swallowing → Task 7 + Global Constraints. ✓
- Honesty wrapper (claim_vs_reality principle) → Task 10. ✓
- Testing (per-detector units, stubbed CLI, red→green integration) → Tasks 1–7, 9–11 units; Tasks 8/12 capability verification. ✓
- Rollout / dogfooding → Tasks 8, 12, 13. ✓

**Placeholder scan:** No "TBD/TODO/implement later"; every code step contains runnable code. ✓

**Type consistency:** `Finding`, `DocsConfig`, `find_stale_docs`, `format_findings`, `DirectionVerdict`, `ClaudeUnavailable`, `review_doc_direction`, `format_verdicts`, and helper names (`_detect_*`, `_front_matter`, `_verify_evidence`, `_claude_cli_runner`) are used identically across tasks. ✓

## Execution Handoff

(Filled in by the brainstorming→writing-plans flow after the plan is saved.)
