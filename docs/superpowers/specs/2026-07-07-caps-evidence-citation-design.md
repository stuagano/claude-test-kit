# `caps evidence` — Citable Capability Proof Design

**Date:** 2026-07-07
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill
**Builds on:** the capability layer (manifest, ledger, gate) — all shipped.

## Problem

`caps` already proves a project's claims and records the proof in `.ctk/ledger.json`.
Separately, knowledge-capture workflows (e.g. the `compound-engineering` plugin's
`/ce-compound`) document solved problems in `docs/solutions/*.md`, re-deriving from
scratch whether a fix "actually works" — even when a `caps` capability already proved
exactly that. There's no way today to hand a capability's proof to a doc-writing
workflow as ready evidence; you'd have to read `.ctk/ledger.json` and `capabilities.yaml`
by hand and cross-reference them yourself.

`caps evidence <id>` closes that gap: one command that prints everything needed to cite
a capability's proof in a doc — the promise (`given`/`when`/`then`), how it's checked,
and its current state — without changing anything about how or where docs get written.

## Non-goals

- No new frontmatter field in `ce-compound`'s doc schema (`references/schema.yaml` there
  is a fixed, canonical contract this project doesn't own). Citations live in a doc's
  prose body, not frontmatter.
- No coupling to `compound-engineering` or any specific plugin. `caps evidence` is
  useful standalone (`caps status` already covers the "what's proven right now"
  question; this covers "give me the citable detail for one capability").
- No new proof/verification logic. This is a read-only formatter over data `status`
  already computes (`capability_state`, ledger entries).

## Command

```
caps evidence <id> [--json]
```

Text output — fixed field order, one capability:

```
$ python -m caps evidence docs-direction-current
id:          docs-direction-current
description: docs still match the project's current direction (LLM review, evidence-verified)
given:       the docs and the current authoritative sources
when:        the LLM direction review runs and its evidence is verified
then:        no doc is left in an un-acknowledged overtaken state
tier:        live
check:       checks/test_docs_direction.py::test_no_overtaken_docs
state:       waived
at:          2026-07-07T23:24:12+00:00
waiver:      until 2026-07-08T23:24:12+00:00 — this session is itself a claude-CLI subprocess...
```

Field rules:
- `id` through `check` always print (straight from the manifest; `check` renders
  `check_target` regardless of `check_kind`, since both a pytest node and a shell
  command are equally citable as "how this is proven").
- `state`/`at`/`duration` mirror what `capability_state` + the ledger entry report
  (`at`/`duration` omitted when never-proven — there's no entry yet).
- `detail` prints only when `state` is `fail` or `error` (the recorded failure snippet).
- `waiver` prints only when `state` is `waived` (reason + `until`).
- `changed` prints only when `state` is `code-stale` (the same changed-deps list
  `status` shows).

`--json` emits the same fields as a dict — extends the existing `_capability_report`
helper (used by `cmd_status`) with `description`/`given`/`when`/`check`, which `status`
never needed but evidence does.

Unknown id → `error: no capability with id <id>` on stderr, exit 2 — same contract as
`caps ack` and `caps verify --capability`.

## Implementation shape

- `cli.py`: new `cmd_evidence(root, now, cap_id, as_json)` — loads manifest + ledger
  (same two calls every other command makes), finds the capability by id (reuse the
  "no capability with id" error pattern from `cmd_ack`), computes `state` via
  `capability_state`, builds the report via `_capability_report` plus the four extra
  manifest fields, then either `json.dumps` or prints the fixed-order text block.
- `main()`: new `evidence` subparser, positional `capability` id + `--json` flag
  (same shape as `ack`'s positional + flag).
- No changes to `manifest.py`, `ledger.py`, `state.py`, or `gate.py` — this reads
  through their existing public surface only.

## Docs

- `SKILL.md`, in the "Capability verification" section: a short paragraph — when
  documenting a solved problem that involves a proven capability (via `/ce-compound`
  or any similar compounding/knowledge-capture workflow), run `caps evidence <id>` and
  fold the relevant fields into the doc's body (there's no frontmatter slot for this;
  it reads naturally under a "Prevention" / "Why This Matters" / "Related" section)
  instead of re-deriving whether the thing actually works from scratch.
- `README.md`: one row added to the existing `caps` command table.

This is the only integration point with `compound-engineering` — a documented
convention in files this project already owns and that `caps init` vendors into every
adopting project. No changes to the `compound-engineering` plugin cache itself: it's a
versioned, shared third-party install (`~/.claude/plugins/cache/...`), and editing it
directly would be silently overwritten on the next plugin update and would affect every
project on the machine, not just this one.

## Testing (dogfood ctk)

New capability in this kit's own `capabilities.yaml` (tier: `cheap` — deterministic,
no live dependency):

```yaml
- id: evidence-prints-ledger-fields
  description: caps evidence prints the manifest+ledger fields for a capability, matching the ledger record
  given: a capability with a recorded ledger entry
  when: caps evidence <id> runs
  then: every printed field (state, tier, check, at, and state-specific detail/waiver/changed) matches the ledger and manifest
  tier: cheap
  deps: [caps/cli.py]
  check: checks/test_cli_evidence.py::test_evidence_matches_ledger
```

`tests/unit/test_cli_evidence.py` (or added to the existing CLI test file, whichever
matches how `status`/`ack` are currently tested) covers:
- a `proven` capability — every field present, no `detail`/`waiver`/`changed`.
- a `waived` capability — `waiver` present with `reason`/`until`.
- a `fail`/`error` capability — `detail` present with the recorded snippet.
- a `never-proven` capability — `at`/`duration` absent, no crash.
- unknown id — exit 2, stderr message, no traceback.
- `--json` — same fields, valid JSON, round-trips through `json.loads`.

## Scope

In: `caps evidence <id>` (text + `--json`), the SKILL.md/README documentation, the
dogfood capability + test. Out: any change to `compound-engineering` or its doc schema,
any new ledger/manifest fields, batch/multi-id evidence output.
