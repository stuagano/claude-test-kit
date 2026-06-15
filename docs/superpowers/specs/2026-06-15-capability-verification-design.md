# Capability Verification — Design

**Date:** 2026-06-15
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill

## Problem

Vibe-coding work routinely *claims* a capability that isn't actually true: "the
job writes to the DB," "the endpoint works," "the deploy is live." The expensive
failure is confident-but-wrong. We want a way to **declare the capabilities a
project promises and prove them against reality** — and, critically, to keep
that proof **in band**: enforced by the harness so it can't be quietly skipped,
rather than relying on the agent to remember.

This is thread #3 of a broader goal (faster iteration, docs that stay current,
claims that are true). #1 and #2 are out of scope here.

## What this is (and isn't)

It is, deliberately, BDD-shaped: a capability check is `setup → action →
readback/assert → teardown`, i.e. Given/When/Then; the manifest is the readable,
executable "what this project promises" contract; running it as tests is living
documentation that can't rot.

It is **not** a reimplementation of Cucumber/Behave. We borrow Gherkin's
*vocabulary* (a `given/when/then` field per capability) but not its
step-definition plumbing — checks are plain `ctk` pytest tests (or shell
commands), which avoids Cucumber's NL→glue translation tax that pays off only
when non-technical readers consume the features (they won't here).

The genuinely novel parts — what justify building anything at all — are the two
things BDD runners don't do:
1. **In-band enforcement against an AI agent** (a Stop hook that gates "done").
2. **Agent-driven discovery** (spot a missing capability, propose it, user approves).

These wrap a runner; they don't replace it.

## Architecture

Two layers, one repo:

- **`ctk/` stays a pure, copy-in primitive library** (`run`, `expect`,
  `Artifact`/`verify`, `claim_vs_reality`, `find_swallowed_exceptions`). Unchanged
  in spirit — no stateful/CLI/enforcement concerns leak into it.
- **A new sibling capability layer** in the same repo carries the opinionated
  framework: manifest format, runner/CLI, ledger, freshness logic, the discovery
  skill behavior, and the Stop hook. It *uses* ctk primitives.

Split of responsibilities:

- **Tooling is global** — the `ctk` skill (discovery know-how), the Stop hook
  (enforcement), and the `ctk verify` runner live in the globally-wired kit
  (`~/.claude/skills/ctk` → this repo). Wire once.
- **Artifacts are per-project** — `capabilities.yaml`, the `checks/`, and
  `.ctk/ledger.json` are committed in each repo. A project with a
  `capabilities.yaml` gets enforced; a project without one is untouched (the hook
  no-ops).

The in-band loop:

```
   (user + agent work)
        │  agent spots a candidate from context
        ▼
  "writes X but never reads back — add a capability?"  →  user approves
        │
        ▼  appended (state: never-proven)
  capabilities.yaml  ──►  checks/ (ctk pytest test or shell)  ──►  .ctk/ledger.json
   (the contract)         (write → readback → teardown)            (proof record)
        ▲                                                              │
        │  on Stop, hook asks: are the relevant capabilities proven & fresh?
        └──────── blocks "done" (in band) if any are red / stale / unproven ◄──────
```

## Manifest — `capabilities.yaml`

The authoritative, human-readable contract. Example:

```yaml
capabilities:
  - id: writes-to-lakebase                 # stable slug; used in ledger + hook output
    description: The ingest job writes order rows to Lakebase and they read back
    given:  a reachable Lakebase instance "orders-db"
    when:   the ingest job runs with --target orders
    then:   the written rows are readable back with matching ids
    tier:   live                           # cheap | live
    deps:   [ingest.py, "lib/db/**"]       # globs; what this capability exercises
    # freshness defaults by tier (see below); override per-capability if needed:
    # freshness: 24h
    check:  checks/test_lakebase_write.py::test_write_readback
```

Field notes:

- `given/when/then` are prose for the human (and future agent) — the readable
  contract. They are not executed.
- `tier`: `cheap` (no live resources, fast, deterministic) or `live` (needs real
  infrastructure).
- `deps`: globs naming the source the capability exercises. Drives fingerprint
  freshness. If omitted, falls back to hashing only the check file, and the runner
  **warns** that coverage is under-declared.
- `check`: either
  - a **pytest node** — `path/to/test.py::test_name`, or
  - a **shell command** — `check: { shell: "./scripts/prove_deploy.sh orders-app" }`
    where exit 0 = proven.

## A check

A check implements `write → readback → teardown`. The safety/isolation strategy
lives *inside each check* (scratch namespace, sentinel row + cleanup, dev target
— per the capability), not in the framework. Example (pytest + ctk):

```python
def test_write_readback(lakebase):              # fixture supplies a scratch target
    ctk.run(["python", "ingest.py", "--target", "orders"]).ok().no_stderr_errors()
    rows = lakebase.read("orders", ids=[CANARY_ID])
    ctk.claim_vs_reality(
        claimed_success=True,
        verifier=lambda: ctk.verify(rows_match(rows, CANARY)),   # reality, read back
        claim_label="writes-to-lakebase",
    )
    lakebase.delete("orders", ids=[CANARY_ID])                   # teardown
```

`tier` (in the manifest, not a pytest marker) tells the runner *when* to run it;
the check itself is plain ctk. No new check DSL.

## Freshness & the ledger

Freshness answers "is this proof still good enough to trust?" — and the right
answer differs by tier:

- **cheap → `freshness: code` (fingerprint), default.** Fingerprint = hash of the
  check + every file matched by `deps`. Fresh = current fingerprint equals the
  last-pass fingerprint. Touch a dep → stale → must re-prove. Honest for
  local/deterministic checks.
- **live → `freshness: 24h` (time-based), default.** A live capability can break
  with **zero code change** (revoked permission, deleted instance, schema drift),
  so code-hash freshness would assert false confidence — the exact failure mode
  this tool exists to kill. Time-based proof *expires*. Overridable per-capability.

`.ctk/ledger.json` — the proof record, **committed by default** (so CI / another
machine sees current state):

```json
{
  "writes-to-lakebase": {
    "result": "pass",                 // pass | fail | error | waived
    "at": "2026-06-15T07:30:00Z",
    "fingerprint": "sha256:…",        // for code-freshness capabilities
    "tier": "live",
    "waiver": null                    // or { "reason": "...", "until": "..." }
  }
}
```

## Runner — `ctk verify`

- `ctk verify` — run the **cheap** tier plus any **stale** live ones; update the
  ledger; **nonzero exit** on any failure.
- `ctk verify --capability <id>` — run exactly one (what the agent runs when the
  gate flags something).
- `ctk verify --status` — **read-only** table, no execution: ✅ proven & fresh /
  ⚠️ stale / ❌ failed / ⏸ waived / — never proven.
- `ctk verify --ack <id> --reason "…"` — record a time-boxed waiver (see Error
  handling).

The runner is independently valuable run **by hand** — this is the MVP (see
Phasing).

## Enforcement — the Stop hook

A global Stop hook (`~/.claude/settings.json`) is what keeps verification in band.

**Confirmed contract** (Claude Code Stop hook): fires at the end of **every**
assistant turn (not only at task completion); blocks via `exit 2` + stderr (or
`{"decision":"block","reason":"…"}` + exit 0), with the `reason` fed back to the
agent as its next instruction; `stop_hook_active` in the hook input guards against
the consecutive-block cap; input includes `cwd` and `transcript_path`.

Because it fires every turn, the hook must be **fast and read-only by default** —
running the suite on every turn (or blocking a clarifying question mid-refactor)
would make it something you rip out, defeating the purpose.

```
On Stop / SubagentStop:
  1. stop_hook_active == true?  → exit 0   (avoid block-loop)
  2. find capabilities.yaml (walk up from cwd)
        └─ none?               → exit 0   (no-op; unmanaged projects untouched)
  3. did this session edit any capability's deps?
        (fast: inspect this turn's edits; fallback: `git status --porcelain`)
        └─ no edits to deps    → exit 0   (read ledger only; nothing to re-gate)
  4. for the affected capabilities:
        cheap tier → RUN now (fast, no live resources) and update ledger
        live tier  → FRESHNESS-CHECK only (do NOT run — too slow / needs infra)
  5. all affected green & fresh (or actively waived)?  → allow "done"
     any red / stale / unproven / error?
        → BLOCK; hand the reason back in band, e.g.:
          "✗ writes-to-lakebase is stale (ingest.py changed since last proof).
           Then: written rows read back with matching ids.
           Run: ctk verify --capability writes-to-lakebase"
```

The asymmetry is the core trick: **cheap checks the hook runs itself; live checks
it refuses to take on faith but will not run for you** — it blocks and tells the
agent (or CI) to prove it deliberately. Fast enough to never want bypassing; live
writes still can't slip through unproven.

**Time-expiry vs. the Stop trigger (deliberate decision).** A live capability can
go stale purely by the clock (`24h` elapsed) with no code edit. The Stop hook
intentionally does **not** block idle/conversational turns on time-expiry alone —
gating every turn after 24h, including pure Q&A, is precisely the nagging that
gets a hook ripped out. Instead: the Stop gate fires only when this session edited
a capability's `deps`, and at that point it checks **both** fingerprint change and
time-expiry for the affected live capabilities. Time-expiry of an *untouched* live
capability is surfaced by `ctk verify --status` and is the natural job of CI / a
scheduled run — not of the per-turn completion gate. (If experience shows this is
too lax, a future option is a once-per-session — not per-turn — nag on any expired
live capability.)

Default scope is the main `Stop`. `SubagentStop` gating is optional (can be
noisy); subagent work is verified at the parent's Stop regardless.

## Discovery — spot → propose → approve

Lives in the `ctk` skill as **advisory** agent behavior. The agent never
auto-edits the manifest. When the agent sees capability-shaped work (writes /
creates / deploys with no readback), it surfaces a draft entry and asks:

```
This ingest job writes to Lakebase but nothing reads it back to confirm.
Proposed capability:
  id: writes-to-lakebase   tier: live   deps: [ingest.py]
  given/when/then: …
  check: read-after-write test (write canary → read → delete)
Add it to capabilities.yaml?  [y/n]
```

On **yes**: append to `capabilities.yaml`, scaffold the check stub, set `deps`.
The capability enters as **never-proven**, so the gate immediately requires a real
run — adding it never fakes the proof. The user is the sole gatekeeper. (A
`PostToolUse` nudge to auto-trigger this is possible later; v1 relies on the agent
spotting it from context.)

## Error handling

- **Live resource unreachable** → recorded as `error` (distinct from `fail`),
  **never** silently passed. Gate blocks with a clear cause ("couldn't reach
  orders-db") so an environment problem is distinguishable from a regression.
- **Acknowledged waiver (pressure-release valve)** → `ctk verify --ack <id>
  --reason "offline, no infra"` records a **time-boxed** waiver (a distinct state,
  not a pass) that expires. The hook respects an active waiver but surfaces it
  loudly (`⚠ writes-to-lakebase waived 2h ago`). A gate with no legitimate escape
  is one that gets ripped out — the waiver keeps honest skips visible and
  self-expiring, preserving in-band trust.
- **Manifest points at a missing/renamed check** → hard misconfig error; gate
  blocks with "check not found."
- **`deps` omitted** → runner warns (under-declared coverage); still functions
  hashing the check file alone.

## Testing the capability layer (dogfood ctk)

- **Unit:** fingerprint computation; freshness/staleness (both modes); manifest
  parse + validation; ledger read/write; tier selection; `check` polymorphism
  (pytest node vs shell); waiver expiry.
- **Integration:** a fixture project with a deliberately-broken capability →
  assert the gate **blocks**; fix it → assert it **passes** (same spirit as the
  existing `buggy_word_count` demo).
- **Hook:** simulate `Stop` against green / stale / waived / no-edits-this-turn
  ledgers → assert the allow/block decision and the reason handed back.

## Phasing (MVP cut)

The hook is the riskiest, most-likely-to-iterate piece; the runner is useful
without it. Sequence (final ordering deferred to the implementation plan):

1. **MVP — manual:** manifest schema + parser/validator, `ctk verify` +
   `--status`, fingerprint & time-based freshness, ledger, checks (pytest + shell),
   `--ack` waivers. Fully usable run by hand; de-risks everything below.
2. **Enforcement:** the Stop hook (fast path, read-only default, deps-edit
   trigger, block/reason, `stop_hook_active` guard), wired globally.
3. **Discovery:** the `ctk` skill's spot→propose→approve behavior.

## Naming defaults

`capabilities.yaml` (manifest), `.ctk/ledger.json` (ledger), `ctk verify` (CLI),
"capability layer" (the sibling package). Open to change.

## To verify at implementation time

- Whether the Stop hook stdin payload includes this turn's tool calls (for the
  fast "did we edit deps this turn?" path). If not present, fall back to `git
  status --porcelain` / transcript inspection — the design holds either way.
- Exact block-loop cap behavior and any env override, before relying on repeated
  blocks.
```
