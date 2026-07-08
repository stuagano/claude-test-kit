# Artifact Steering — Generalizing `caps` for Growing Structured Artifacts

**Date:** 2026-07-08
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill
**Builds on:** the full capability layer (manifest, ledger, fingerprint, gate, `caps evidence`), all shipped.

## Problem

Databricks' Vibe Data Modeling ([blog post](https://www.databricks.com/blog/reimagining-data-modeling-lakehouse-introducing-vibe-data-modeling))
generates a business-specific data model from plain English, but the durable idea
in it isn't data modeling — it's a generic pattern for keeping an agent honest while
it iteratively builds one large structured artifact (there: `model.json`):

- A **deterministic rule engine** (251 rules) validates the actual artifact, never
  the LLM's self-report.
- An **agentic loop** that, on failure, changes strategy rather than repeating a
  fix, routing to a **sandboxed repair step** before revalidating.
- **Two-tier review** — a local pass per part of the artifact, a global pass for
  cross-cutting integrity (duplicates, structural consistency) — bounded to a
  fixed number of iterations.
- **User intent always wins** — an explicit instruction overrides any rule or
  heuristic.
- **Append-only versioning** — iterations never overwrite prior state.

`caps` already has three of the load-bearing pieces this needs: a declared
manifest as source of truth, a deterministic check-and-record ledger, and a gate
that blocks "done" on an unproven claim. What it lacks is exactly the three
mechanisms above: a repair loop, bounded two-tier review, and a priority
hierarchy for user intent. This design adds those three, reusing everything else.

## Framing constraint

**`caps` has no LLM inside it — it is a verification harness an agent calls.**
Vibe's "agentic loop" and "up to eight passes" run inside one LLM backend; caps
can't replicate that as an internal loop. Every mechanism below is instead a
**call-and-response protocol**: caps tracks state (attempted strategies, pass
counts) across repeated invocations by the calling agent, and refuses to let the
agent fake progress — it never trusts a claim it can't itself verify, exactly
caps's existing philosophy, just extended to cover "is this retry actually new."

## Non-goals

- No new manifest file. Every addition below extends `capabilities.yaml` and
  `.ctk/ledger.json`, or adds one small sibling file — never a parallel
  `artifacts.yaml` system.
- No semantic conflict resolution between two directives that each pass their
  own check but represent contradictory intent. Caps proves each directive's own
  check; adjudicating a contradiction between two passing directives is a
  human/modeling problem, out of scope here.
- No change to any existing command's behavior. `status`, `verify`, `ack`,
  `evidence`, `doctor` are all unaffected — `group`/`role` are optional fields
  that default to today's behavior when absent.

## Design

### 1. Data model changes

- **`Capability` gains two optional fields**: `group: str | None` and
  `role: "local" | "global"` (default `"local"`). A rule in an artifact's rule
  set is just a capability with these set — `given/when/then/tier/deps/check`
  are unchanged and mean exactly what they mean today.
- **A new optional top-level `groups:` manifest section**, sibling to
  `capabilities:`:
  ```yaml
  groups:
    ontology-domains:
      max_passes: 8       # optional; default 8 if a group is used but undeclared
  capabilities:
    - id: rule-047
      group: ontology-domains
      role: local
      ...
  ```
  Declares per-group settings — just `max_passes` for now. A group used without
  a `groups:` entry falls back to the default of 8.
- **`LedgerEntry` gains `strategies_tried: list[str]`** — every attempted fix
  from `caps repair apply` is appended, never cleared. This is what lets caps
  mechanically detect a repeated fix instead of trusting the agent's word.
- **A new append-only sibling file, `.ctk/directives.json`** (same atomic
  temp-write-then-rename pattern as `ledger.py`) — one entry per user
  instruction, entries never edited or removed (see §4).

Existing projects and manifests are unaffected — every new field and file is
additive and optional.

### 2. `caps repair` — the sandboxed repair loop

A start/apply protocol, not an internal loop:

**`caps repair start <group>`** — creates (or reuses) a git worktree for the
group, and prints the group's current failures plus every strategy already
recorded in `strategies_tried` for those failing capabilities. The agent edits
the artifact inside the worktree, never the real tree directly.

**`caps repair apply <group> --strategy "<short description>"`**:
1. If `<description>` matches (normalized — lowercased, whitespace-collapsed,
   near-exact string comparison; no LLM judgment inside caps) a strategy already
   recorded as tried-and-failed for any still-failing capability in this group,
   **refuse immediately, no re-run**: "you already tried this, vary your
   approach."
2. Otherwise, run the group's capability checks with `root` = the worktree —
   `run_capability(cap, root)` already accepts an arbitrary root; this is the
   same function `verify` calls, just pointed elsewhere. This step reuses
   `verify`'s existing waiver-skip semantics unchanged: a capability under an
   active directive waiver (§4) is never re-run or treated as needing a fix —
   `repair` only ever acts on capabilities that are actually failing.
3. All pass → promote: merge the worktree's artifact changes into the real tree
   using this repo's existing merge conventions, not a bespoke file-copy scheme.
   Record the winning strategy in the ledger.
4. Still failing → append the strategy to `strategies_tried` for whichever
   capabilities still fail, return the failure detail, do **not** promote — the
   worktree stays live for another attempt.

If a worktree can't be created (not a git repo, dirty tree), `caps repair start`
**fails closed** with a clear message rather than silently allowing an
unprotected ad hoc edit — the whole point of `repair` is the strategy-dedup
guarantee, and caps should never pretend to provide it when it can't.

### 3. `caps review <group>` — bounded two-tier review

Read-only diagnosis with a bounded loop and staged scope — no mutation, unlike
`repair`:

1. Run all `role: local` capabilities in the group against the real tree.
2. If all locals pass, run the group's `role: global` capability — cross-cutting
   checks (duplicates, naming collisions, structural integrity) that no single
   local rule can see. Exactly one `global` capability is allowed per group;
   manifest loading rejects a second, the same way it already rejects a
   duplicate capability id.
3. On any failure, report it (same shape as `caps status` today) and increment a
   pass counter for the group in a new sibling file, `.ctk/review_sessions.json`
   (same atomic-write pattern): `{group: {passes, last_result, at}}`.
4. Once clean, the counter resets to 0. If it reaches the group's `max_passes`
   (from `groups:` in the manifest) while still not clean, the session is marked
   `passes_exhausted: true` — a state distinct from an ordinary failing
   capability.

Local and global are the same rules at different scope, not competing
opinions — global simply sees what local structurally cannot. There is no
conflict to resolve between them; actual conflicting verdicts are §4's job.

`passes_exhausted` plugs into the **existing** Stop-hook gate as a blocking
condition, distinct from a plain fail: the message tells the agent to stop and
escalate to a human, not attempt another automated pass. No second enforcement
path is introduced.

### 4. `caps directive` — user intent as the priority hierarchy

A directive is a user's plain-English instruction, compiled into a checkable
assertion, with absolute priority over any rule it conflicts with. It needs
almost no new machinery — it's a capability (given/when/then + check) plus
caps's *existing* waiver mechanism, plus an append-only log instead of an
editable manifest row:

- `caps directive add --text "..." --check <pytest-node> [--waives rule-047,rule-112]`
  appends one entry to `.ctk/directives.json`: the raw text, the check, its
  first verification result, and which rule-capability-ids (if any) it waives.
  Entries are never edited or removed — superseding a directive means adding a
  new entry, matching Vibe's "versions never overwrite."
- Any rule id in `--waives` is **pre-waived** for as long as the directive's own
  check keeps passing — an ordinary `caps ack`-style waiver, just sourced from a
  directive instead of a human running `ack` directly, and tied to the
  directive's check rather than a fixed expiry. If the directive's check ever
  starts failing, the waiver lapses immediately and the underlying rule is live
  again.
- This *is* the priority hierarchy: directive-sourced waivers sit above rule
  capabilities, which sit above any unverified agent claim (which caps already
  refuses to trust everywhere else). No separate "priority" field or resolution
  engine — it falls out of waiver-precedence plus the append-only log.

## Testing (dogfood ctk)

- **Unit**: `group`/`role` parsing on `Capability`; the new `groups:` manifest
  block; `strategies_tried` ledger append behavior; `directives.json`
  load/save/round-trip (atomic, append-only).
- **Integration**: full `repair start` → `apply` loop against a fixture group (2
  local + 1 global rule on a toy artifact) — first failing apply records the
  strategy and doesn't promote; repeating that exact strategy is refused with no
  re-run; a genuinely different strategy that passes promotes and merges into
  the real tree.
- **Integration**: `caps review <group>` against a fixture that takes exactly 3
  passes to go clean — asserts the pass counter increments and resets correctly;
  a separate test asserts `passes_exhausted` fires once the configured cap is
  hit.
- **Integration**: add a directive waiving a rule, confirm `verify`/`status`
  treat it as waived; make the directive's own check fail, confirm the waiver
  lapses and the rule goes live again on the next verify.
- New dogfood capabilities in this kit's own `capabilities.yaml` proving each
  command, matching the existing convention (e.g.
  `repair-refuses-repeated-strategy`, `review-tracks-bounded-passes`,
  `directive-waiver-lapses-with-check`).

## Scope

In: `Capability.group`/`.role`, the `groups:` manifest section,
`strategies_tried` on `LedgerEntry`, `.ctk/directives.json`,
`.ctk/review_sessions.json`, `caps repair start`/`apply`, `caps review <group>`,
`caps directive add`, and the gate's new `passes_exhausted` blocking condition.

Out: any new top-level manifest file, semantic conflict resolution between two
passing directives, changes to any existing command's behavior, and wiring up a
concrete artifact (ontology or otherwise) — this design covers the mechanism
only, per the "general mechanism, no specific artifact yet" scoping decision.
