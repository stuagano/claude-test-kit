# Doc Staleness Detection — Design

**Date:** 2026-06-17
**Status:** Approved (design)

## Problem

Docs drift from reality and nobody catches it. A status doc cites a number that
changed; a guide references a file that moved; a progress tracker that was a
point-in-time snapshot never gets retired. This is the same failure ctk already
targets — **a claim that no longer matches reality** — but applied to prose
instead of program output. Standard tooling doesn't check it at all.

This was surfaced concretely by a doc-cleanup audit of this repo (superseded
status docs, abandoned trackers, shipped specs left lying around). The insight:
doc staleness is itself a capability the project should *prove*, not a chore
someone remembers to do.

## Goal

Catch **all flavors of doc staleness** — drift, dead internal links, orphaned
docs, and abandonment — with a reusable ctk primitive and a thin caps
capability that proves the docs aren't stale, in band, on every turn.

## Approach (chosen: A)

A new ctk primitive does the detection; a `docs-current` caps capability proves
it. This matches the existing layering — **ctk = reusable copy-in primitive**
(mirrors `find_swallowed_exceptions`), **caps = thin proof on top** — so every
project that vendors the kit gets it, and the detection is unit-testable in
isolation.

Rejected alternatives: a CLI-first surface (subprocess is harder to unit-test
and less composable; can be layered on later), and pure-caps logic inside the
check (not reusable, breaks the ctk/caps split).

## Settled design forks

- **Scope:** all flavors — drift, dead internal links, orphans, abandonment.
- **Mechanism:** hybrid — heuristic scan for the cheap universal wins, plus
  optional explicit front-matter assertions for semantic claims a scan can't
  infer.
- **Abandonment:** heuristic age + shape (no annotation), kept honest by
  measuring age against the repo's latest commit, not wall-clock.

## Component 1 — the `ctk/docs.py` primitive

### Function

```python
def find_stale_docs(
    doc_roots=("docs/", "README.md", "SKILL.md", "CLAUDE.md"),
    repo_root=".",
    config: DocsConfig | None = None,
) -> list[Finding]
```

Zero-config call works out of the box; everything tunable via `DocsConfig`.

### The `Finding` dataclass

| field | meaning |
|---|---|
| `doc` | path to the doc |
| `line` | line number, or `None` |
| `kind` | `broken_ref` · `dead_link` · `orphan` · `abandonment` · `assertion_failed` |
| `severity` | `error` (blocks) · `warn` (advisory) |
| `message` | human-readable |
| `evidence` | the offending token / referent |

### The five detectors

1. **`broken_ref` (drift — error).** Markdown link targets that are relative
   repo paths, and inline `` `code spans` `` that *strongly* look like a repo
   path (start with a known top-level dir — `caps/ ctk/ bin/ tests/ docs/
   examples/` — or contain a slash plus a tracked extension). Referent missing →
   error. Conservative on purpose: placeholders (`path/to/…`, `<...>`,
   `example/…`) and a config `ignore` list are skipped so illustrative paths
   don't false-positive.

2. **`dead_link` (internal links — error).** Relative links to other repo docs.
   Missing target file → error. Missing `#anchor` within an existing target →
   `warn` (anchors are fuzzier).

3. **`orphan` (reachability — warn).** Build the internal doc-link graph from
   `entrypoints` (README/SKILL/CLAUDE by default). Docs unreachable from any
   entrypoint → warn. Archival trees (`docs/superpowers/**`) exempt by default —
   an intentional flat archive, not cross-linked.

4. **`abandonment` (heuristic age + shape — warn).** Candidate when a doc
   matches a **shape** (lives in `status/ reports/ progress/`, or contains
   snapshot markers like `___%`, `current-state`, a bare month-year,
   "superseded") **AND** is **old** — last git-commit older than `max_age_days`
   (default 90), measured against the repo's most recent commit, never
   wall-clock. Both signals required.

5. **`assertion_failed` (the hybrid escape hatch — error).** Docs may carry
   checkable claims in YAML front-matter under `ctk:` — `requires_paths: [...]`
   (must exist) and `requires_grep: [{file, pattern}]` (file must match — for
   semantic claims like "the default window is 24h"). Failed assertion → error.
   Just these two assertion types to start (YAGNI).

### Severity model

`error` = objective drift (broken refs, dead links, failed assertions) →
**blocks** the `docs-current` capability. `warn` = fuzzy/subjective (orphan,
abandonment) → **surfaced but doesn't block**, consistent with choosing the
heuristic abandonment route knowing it's noisy. Severity is overridable in
config, so abandonment can be promoted to a hard error later if it proves
trustworthy.

## Component 2 — `DocsConfig`

A dataclass, zero new deps (PyYAML already present). Fields: `doc_roots`,
`entrypoints`, `ignore` (path-ref skip globs/regexes), `orphan_exempt` (default
`["docs/superpowers/**"]`), `abandonment` (`shape_dirs`, `markers`,
`max_age_days=90`, `exempt`), and `severity_overrides`. Loadable from an
optional `docs-policy.yaml` at repo root via `DocsConfig.from_yaml(...)`; absent
→ baked-in defaults. The check loads the file if present, else uses defaults — so
the kit works with no policy file. Defaults are chosen to fit this repo's layout.

## Component 3 — the `docs-current` caps capability

```yaml
- id: docs-current
  description: project docs don't drift from reality (no broken refs, dead links, or failed doc assertions)
  given: the docs tree and the code it describes
  when:  the docs staleness scan runs
  then:  no error-severity findings
  tier:  cheap
  deps:  ["docs/**","README.md","SKILL.md","CLAUDE.md","caps/**","ctk/**","bin/**","docs-policy.yaml"]
  check: tests/unit/test_docs_current.py::test_no_stale_docs
```

```python
def test_no_stale_docs():
    errors = [f for f in find_stale_docs() if f.severity == "error"]
    assert errors == [], format_findings(errors)
```

**Why `cheap` is honest here:** the capability asserts only on `error` findings
(drift/links/assertions), all deterministic functions of `deps`.
Abandonment/orphan (`warn`) use git dates / graph reachability and are *advisory
output*, not part of pass/fail — so they never make the proof flap, and the
fingerprint stays meaningful.

## Determinism & error handling

- **Age via injected clock.** The repo-head date is obtained through a small
  injectable provider so unit tests pin it — no wall-clock, reproducible in CI.
- **No swallowing.** An unreadable doc or malformed front-matter becomes a
  `Finding` (`error`, "could not read/parse"), never a silent skip or a crash.
  The scanner that hunts silent failure must not commit one.

## Testing

Unit tests (the `workspace` fixture, in the style of `tests/unit/test_ctk_unit.py`),
one per detector against synthesized docs in a temp dir:

- broken ref flagged / valid ref clean / illustrative path ignored
- dead link flagged
- orphan graph reachability
- abandonment requires *both* shape and age (injected dates / tiny fake-git)
- front-matter assertion pass and fail
- unreadable doc → finding, not exception

Plus an integration test that `caps verify --capability docs-current` goes
red → green.

## Rollout

First real run targets this repo: the audit that kicked this off becomes the
feature's first proof. Drifted docs surface as `broken_ref`/`dead_link` errors
to fix; the snapshot junk surfaces as `abandonment` warns — the cleanup list
that started this. The doc-staleness problem ends up handled by the kit,
dogfooded.

## Out of scope (YAGNI)

- Assertion types beyond `requires_paths` / `requires_grep`.
- A standalone CLI surface (`python -m ctk docs`) — layerable on top later.
- External (http) link checking.
- Anchor-existence as a hard error (kept as `warn`).
