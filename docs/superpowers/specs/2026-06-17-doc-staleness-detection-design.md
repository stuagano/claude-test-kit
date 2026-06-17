# Doc Staleness Detection — Design

**Date:** 2026-06-17
**Status:** Approved (design)

## Problem

Docs drift from reality and nobody catches it. A guide references a file that
moved; a spec describes an approach the project has since abandoned; a status
doc never gets retired after it's overtaken. This is the same failure ctk
already targets — **a claim that no longer matches reality** — but applied to
prose instead of program output. Standard tooling doesn't check it at all.

This was surfaced by a doc-cleanup audit of this repo (superseded status docs,
abandoned trackers, shipped specs left lying around). The insight: doc staleness
is itself a capability the project should *prove*, not a chore someone remembers
to do.

## Goal

Catch **all flavors of doc staleness** — drift, dead internal links, orphans,
and docs the project's **content and direction** have moved past — and prove it
in band. Staleness is **not primarily about age**: a three-week-old doc can be
overtaken while an old one stays accurate. Age is at most a weak corroborating
signal.

## Approach (chosen: A)

A reusable ctk primitive does the detection; caps capabilities prove it. This
matches the existing layering — **ctk = reusable copy-in primitive** (mirrors
`find_swallowed_exceptions`), **caps = thin proof on top** — so every project
that vendors the kit gets it, and the detection is unit-testable in isolation.

The work splits along determinism, which maps onto the kit's two tiers:

- **Mechanical staleness** is objective and deterministic → a `cheap`,
  fingerprint-based capability.
- **Direction** ("has the project moved past this doc?") is a judgment call →
  an **LLM-first**, `live`, time-windowed capability.

## Settled design forks

- **Scope:** all flavors — drift, dead internal links, orphans, direction.
- **Mechanism:** hybrid — heuristic scan for the cheap universal wins, plus
  optional explicit front-matter assertions for semantic claims a scan can't
  infer.
- **Direction:** **LLM-first** (the user's call), kept honest with
  evidence-citation that ctk verifies deterministically. It is **content- and
  direction-driven, not time-driven**; age is only one input the judge weighs
  (~30 days is roughly when age starts to matter, not a gate).

---

## Component 1 — `ctk/docs.py`: deterministic detection

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
| `kind` | `broken_ref` · `dead_link` · `orphan` · `superseded` · `assertion_failed` |
| `severity` | `error` (blocks) · `warn` (advisory) |
| `message` | human-readable |
| `evidence` | the offending token / referent |

### Deterministic detectors

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

4. **`superseded` (explicit retirement — warn).** *Objective* supersession only:
   front-matter `superseded_by:`, or prose "superseded/replaced/deprecated by
   [link]" where the link resolves, or a newer doc with the same spec slug and a
   later date. The doc isn't *broken* — it's been retired and left lying around.
   Warn, promotable to error in config. (The *judgment* of un-marked direction
   drift is Component 3's job, not this one.)

5. **`assertion_failed` (the hybrid escape hatch — error).** Docs may carry
   checkable claims in YAML front-matter under `ctk:` — `requires_paths: [...]`
   (must exist) and `requires_grep: [{file, pattern}]` (file must match — for
   semantic claims like "the default window is 24h"). Failed assertion → error.
   Just these two assertion types to start (YAGNI).

### Severity model

`error` = objective drift (broken refs, dead links, failed assertions) →
**blocks** the `docs-current` capability. `warn` = advisory (orphan, superseded)
→ surfaced but doesn't block. Severity is overridable in config.

---

## Component 2 — `DocsConfig`

A dataclass, zero new deps (PyYAML already present). Fields: `doc_roots`,
`entrypoints`, `ignore` (path-ref skip globs/regexes), `orphan_exempt` (default
`["docs/superpowers/**"]`), `severity_overrides`, and a `direction` block
(model, window, doc selection, exemptions for Component 3). Loadable from an
optional `docs-policy.yaml` at repo root via `DocsConfig.from_yaml(...)`; absent
→ baked-in defaults chosen to fit this repo. The checks load the file if present,
else defaults — so the kit works with no policy file.

---

## Component 3 — `ctk/docs_direction.py`: LLM-first direction review

The judgment Component 1 deliberately doesn't make: **has the project's content
and direction moved past this doc**, regardless of age?

### Function

```python
def review_doc_direction(
    docs: Sequence[str],
    repo_root=".",
    config: DocsConfig | None = None,
) -> list[DirectionVerdict]
```

### How the LLM is invoked — Claude CLI headless

The check shells out to `claude -p` (Claude Code headless) with a structured
prompt: the doc under review, plus the **current authoritative context** —
README / SKILL / CLAUDE, `capabilities.yaml`, the newest specs, and recent
`git log` — and the signals to weigh (done-but-still-framed-as-future,
decisions the current sources contradict, age in days since last commit relative
to repo head). It returns a structured verdict.

- **No new Python dep, no API-key plumbing** — uses the Claude Code auth already
  in the loop. The tool that's already here does the judging.
- **Graceful degradation:** if `claude` isn't on PATH, the `live` capability is
  recorded as ack'd/skipped with a clear reason (`claude CLI unavailable`)
  rather than failing — fail-open, like the rest of the kit.

### The `DirectionVerdict`

| field | meaning |
|---|---|
| `doc` | path |
| `verdict` | `current` · `overtaken` · `uncertain` |
| `rationale` | the LLM's one-line reason |
| `doc_evidence` | exact quoted line(s) from the doc claimed to be overtaken |
| `source_evidence` | exact quoted line(s) from current sources that contradict |

### Keeping the LLM judge honest (the dogfooding part)

An LLM judging staleness is itself an agent step that can silently fail —
rubber-stamp everything, or hallucinate an `overtaken` verdict. This kit exists
to catch exactly that, so the judge does **not** get to self-certify:

- Every `overtaken` verdict **must cite evidence** — `doc_evidence` and
  `source_evidence` as exact quotes.
- ctk **deterministically verifies the citations** via `claim_vs_reality`: the
  quoted lines must actually appear in the named files. A verdict whose evidence
  doesn't check out is discarded as an unproven claim (and logged), not trusted.

The staleness-hunter refuses to let its own LLM commit a silent failure.

---

## Component 4 — the caps capabilities (thin wiring)

### `docs-current` (cheap, deterministic)

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

Cheap is honest: it asserts only on `error` findings, all deterministic
functions of `deps`. `warn` findings (orphan, superseded) are advisory output,
never pass/fail, so the fingerprint stays meaningful.

### `docs-direction-current` (live, LLM-first)

```yaml
- id: docs-direction-current
  description: docs still match the project's current direction (LLM review, evidence-verified)
  given: the docs and the current authoritative sources
  when:  the LLM direction review runs and its evidence is verified
  then:  no doc is left in an un-acknowledged 'overtaken' state
  tier:  live            # 24h window; gate is quiet on time-expiry for live
  deps:  ["docs/**","README.md","SKILL.md","CLAUDE.md","capabilities.yaml"]
  check: checks/test_docs_direction.py::test_no_overtaken_docs
```

```python
def test_no_overtaken_docs():
    verdicts = review_doc_direction(select_direction_docs())
    overtaken = [v for v in verdicts if v.verdict == "overtaken"]  # already evidence-verified
    assert overtaken == [], format_verdicts(overtaken)
```

`live` is the right tier: the verdict is non-deterministic and can change with no
code edit, so its proof expires on the clock (24h). The Stop-hook gate is
**quiet on time-expiry for live** — past the window it's a note, not a per-turn
block — and never-proven/failed still block, so it's proven at least once.
Promoting/acking an overtaken doc (retire it, or `caps ack` with a reason) clears
it.

---

## Determinism & error handling

- **No wall-clock in the deterministic layer.** Component 1 is a pure function
  of repo contents. Any age signal lives only in Component 3 (the LLM input),
  and "days since last commit" is measured against the repo's most recent commit,
  not the clock — reproducible for a given HEAD.
- **No swallowing.** An unreadable doc or malformed front-matter becomes a
  `Finding` (`error`, "could not read/parse"), never a silent skip or crash. The
  scanner that hunts silent failure must not commit one.
- **LLM failures are explicit.** A missing `claude` CLI, a malformed verdict, or
  unverifiable evidence each produce a recorded, visible outcome — never a quiet
  pass.

## Testing

Unit tests (the `workspace` fixture, in the style of
`tests/unit/test_ctk_unit.py`), one per deterministic detector against
synthesized docs in a temp dir: broken ref flagged / valid clean / illustrative
ignored; dead link; orphan graph; explicit supersession (front-matter, prose,
newer-slug); front-matter assertion pass and fail; unreadable doc → finding not
exception.

For Component 3: the `claude` invocation is stubbed (a fake CLI on PATH emitting
canned JSON) so tests are deterministic — assert that good evidence is accepted,
that a verdict with **non-existent** quoted evidence is **discarded** (the
honesty wrapper), and that a missing CLI degrades to ack-skip.

Plus integration tests that `caps verify --capability docs-current` and
`--capability docs-direction-current` each go red → green.

## Rollout

First real run targets this repo: the audit that kicked this off becomes the
feature's first proof. Drifted docs surface as `broken_ref`/`dead_link` errors
to fix; retired snapshots surface as `superseded`/`overtaken` — the cleanup list
that started this. The doc-staleness problem ends up handled by the kit,
dogfooded.

## Out of scope (YAGNI)

- Assertion types beyond `requires_paths` / `requires_grep`.
- A standalone CLI surface (`python -m ctk docs`) — layerable later.
- External (http) link checking.
- Anchor-existence as a hard error (kept as `warn`).
- An Anthropic-SDK invocation path — Claude CLI headless only, for now.
