# Capability Verification — Phase 2 (Stop-Hook Enforcement) Design

**Date:** 2026-06-15
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill
**Builds on:** Phase 1 (`docs/superpowers/specs/2026-06-15-capability-verification-design.md`, MVP runner shipped)

## Problem

Phase 1 shipped the `caps` runner (`verify` / `status` / `ack`) and a SKILL.md that
*reminds* me to use it. But a skill undertriggers — the most important check is the
one that quietly doesn't run. Phase 2 makes capability verification **in band**: a
global `Stop` hook the harness runs on every turn, so a project that declares
capabilities can't have work called "done" while a capability is unproven, failed,
or stale — without relying on the agent's memory.

## Confirmed Stop-hook contract

From the official docs (https://code.claude.com/docs/en/hooks), verified before design:

- **Fires:** at the end of **every** assistant turn (not only task completion).
- **Stdin JSON (minimal):** `session_id`, `transcript_path`, `cwd`,
  `permission_mode`, `hook_event_name` (`"Stop"`), `stop_hook_active` (bool).
  **No `tool_calls`, no edited-files list, no assistant message.**
- **Block:** `exit 2` + stderr, **or** `{"decision":"block","reason":"..."}` at exit 0.
  The `reason` is fed back to Claude as context to act on.
- **Allow:** exit 0 with no block decision. A non-blocking note can be surfaced via
  `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"..."}}`.
- **`stop_hook_active`:** true when a Stop hook is already in a blocking cycle; used
  to avoid infinite loops (there is a consecutive-block cap).
- **Registration:** under `hooks.Stop` in settings.json (global `~/.claude` or
  per-project). No matcher. Supports `timeout`.

**Consequence that shaped the design:** because the payload can't tell us what
changed this turn, the gate does *not* try to detect "edited this session." It
judges state directly and cheaply from the ledger + fingerprints we already built.
Fingerprint mismatch already means "the code changed since we last proved it."

**To verify empirically at implementation time (docs can't guarantee it):** the
`stop_hook_active` *lifecycle* — is it `true` on the immediate re-stop after the gate
blocks (so self-clearing works), and does it reset to `false` on a fresh user turn
(so enforcement re-arms)? This is load-bearing and is step 1 of the plan.

## Consistency with Phase 1 (deliberate)

Phase 1 decided the gate must **not** block idle/conversational turns on time-expiry
alone (the "untouched 25h, then you ask a question, and it blocks" problem).
Phase 2 upholds that: **live time-expiry does NOT block.** The gate blocks only on
states tied to work and correctness. Time-expiry is surfaced (a non-blocking note in
the reason, and via `status`/CI), never enforced at the per-turn boundary.

## Gate behavior (decided)

**Block once per turn, self-clearing.** If a capability isn't OK, block the stop
*once*; `stop_hook_active` then lets the very next stop through so the turn can
finish. A clean+proven repo never blocks. A stale one reminds once per turn until you
re-prove (`caps verify`) or silence it (`caps ack`). The hook is **read-only — it
never runs checks** (no pytest/shell subprocess on a turn boundary); it reads the
ledger and hashes deps only. A blocking state tells me to run `caps verify`; it does
not run it for me.

## Architecture & components

All new code lives in the kit (versioned). The only machine-specific artifact is the
hook entry in `~/.claude/settings.json`.

- **`caps/state.py` — `capability_state(cap, entry, root, now) -> str`.** The single
  source of truth for a capability's state, shared by `status` and the gate so they
  can never disagree. Returns one of:
  - `proven` — last result pass AND fresh.
  - `never-proven` — no entry, or an entry that never passed (incl. expired waiver).
  - `fail` — last result `fail`.
  - `error` — last result `error`.
  - `code-stale` — `freshness: code`, last pass, but current fingerprint ≠ recorded.
  - `time-expired` — duration freshness, last pass, but outside the window.
  - `waived` — an active (unexpired) waiver.

  This refactors the logic currently inline in `cli._status_label`; `cmd_status` is
  updated to call `capability_state`. The *granularity matters*: `code-stale` and
  `time-expired` must be distinct so the gate can block one and not the other (even
  though `status` may render both as "stale").

- **`caps/gate.py` — the decision.** `decide(hook_input: dict, now) -> GateDecision`
  where `GateDecision` is allow or block(reason). Pure and testable: resolve the
  project from `cwd` (fallback to the dir of `transcript_path` if `cwd` isn't a
  project), load manifest + ledger, compute `capability_state` for each, and apply:
  - **BLOCK states:** `never-proven`, `fail`, `error`, `code-stale`.
  - **ALLOW states:** `proven`, `waived`, `time-expired`.
  - `time-expired` capabilities are listed in the reason as a non-blocking note.

- **`caps gate` subcommand.** Reads stdin JSON, calls `gate.decide`, emits the
  decision JSON / exit 0, and **fails open** on any internal error: exit 0 plus a
  visible `additionalContext` note ("caps gate failed: <err> — enforcement skipped
  this turn") so a broken gate is surfaced to me, not silent.

- **`bin/caps-stop-gate.sh` — the registered command.** In order:
  1. **Shell short-circuit:** walk up from `$CLAUDE_PROJECT_DIR`/cwd for
     `capabilities.yaml`; if none found, `exit 0` immediately — *before* launching
     Python. This keeps the 99% of turns in manifest-less projects free of
     interpreter startup.
  2. Otherwise `exec` the kit's venv Python with `PYTHONPATH=<kit>` running
     `python -m caps gate`, piping stdin through.

- **`caps install-hook` / `uninstall-hook`.** Idempotently add/remove our `hooks.Stop`
  entry in settings.json. `install-hook` backs up settings.json first
  (`*.bak.<date>`), never clobbers other hooks or keys, sets a `timeout` (~10s), and
  **verifies the venv Python exists** at install time. `--settings <path>` targets a
  temp file for tests.

## Gate algorithm

```
read stdin JSON
1. stop_hook_active == true?            -> exit 0        (self-clearing)
2. resolve project from cwd (else transcript_path dir); no capabilities.yaml
                                        -> exit 0        (no-op)
3. for each capability: capability_state(cap, ledger.get(id), root, now)
4. blocking = states in {never-proven, fail, error, code-stale}
   expired  = states == time-expired         (non-blocking note)
5. blocking empty?
      yes -> exit 0   (allow; if expired non-empty, optionally emit additionalContext note)
      no  -> print {"decision":"block","reason": <listing of blocking (+ expired note)>}; exit 0
on any exception      -> exit 0 + additionalContext "caps gate failed: <err>"   (fail open, visible)
```

**Reason text** (fed back to me):
```
✗ Capabilities not proven & fresh — resolve before finishing:
  • writes-to-lakebase [code-stale]: written rows read back with matching ids
    → python -m caps verify --capability writes-to-lakebase
  • deploy-live [never-proven]: the app responds after deploy
    → python -m caps verify --capability deploy-live
(note) live capability metrics-export is time-expired (>24h) — re-verify when convenient.
Full status: python -m caps status   ·   can't prove now? python -m caps ack <id> --reason "..."
```

## Error handling & failure modes

- **Internal gate error** (malformed manifest, bad ledger JSON): fail **open** (exit
  0) so a bug never bricks the ability to finish a turn — but surface it via
  `additionalContext` so it's visible to me and I can tell the user.
- **Missing `.venv` Python** (the wrapper depends on the gitignored venv): the wrapper
  falls back to `exit 0` (fail open). Known limitation: enforcement is silently off
  until the venv is rebuilt (`./run_tests.sh`). Mitigations: `install-hook` verifies
  the venv at install time; documented clearly. (A future hardening could have the
  wrapper emit a one-line `additionalContext` when it finds a manifest but no venv.)
- **Self-clear / loop cap:** `stop_hook_active` short-circuits to allow, so we never
  approach the consecutive-block cap. Verified by the empirical step.
- **Fail-open philosophy:** correct for a global every-turn hook — the cost of a false
  block (can't finish any turn) is worse than a missed enforcement, and missed
  enforcement is made visible rather than silent.

## Performance

- Manifest-less project (the common case): pure bash walk-up + `exit 0`. No Python.
- Project with a manifest: one Python process that hashes a handful of files + reads a
  small JSON. Milliseconds. No checks are executed.

## Testing (dogfood ctk on the gate)

- **Unit — `capability_state`:** each state reachable: `proven`; `never-proven` (no
  entry; expired waiver); `fail`; `error`; `code-stale` (fingerprint changed);
  `time-expired` (pass outside window); `waived` (active).
- **Unit — `gate.decide`:** `stop_hook_active=true` → allow; no manifest → allow; all
  proven → allow; one `code-stale` → block, reason names it; `never-proven`/`fail`/
  `error` → block; active waiver → allow; `time-expired` → allow (and appears as a
  note, not a block); cwd-not-a-project but `transcript_path` resolves → works.
- **Unit — fail-open:** malformed manifest / unreadable ledger → allow + an
  `additionalContext` error note (no traceback, no block).
- **Integration — `caps gate` subprocess:** pipe real JSON stdin; assert exit 0 and
  the `{"decision":"block",...}` JSON for a stale project, empty for a clean one.
- **Integration — wrapper short-circuit:** run `bin/caps-stop-gate.sh` in a temp dir
  with no `capabilities.yaml` and assert it exits 0 without invoking Python (e.g., a
  Python that would error if launched, or a sentinel).
- **install-hook:** against a temp settings.json — inserts correctly, idempotent,
  preserves existing keys/hooks, writes a backup, refuses/ warns if venv missing.

## Plan step 1 (empirical, before building the gate)

Install a throwaway `Stop` hook that dumps stdin to `/tmp` and always exits 0. Trigger
turns and record:
1. The exact payload fields (confirm against the documented schema; note `cwd` vs
   project dir).
2. **The `stop_hook_active` lifecycle:** its value on a normal turn, on the immediate
   re-stop after a block, and on a fresh user turn. This determines that
   "block once, self-clearing, re-arm next turn" actually holds. Adjust `gate.py`
   if reality differs. Remove the throwaway hook afterward.

## Scope

In: `caps/state.py`, `caps/gate.py`, `caps gate`, `caps install-hook`/`uninstall-hook`,
`bin/caps-stop-gate.sh`, the `capability_state` refactor of `cmd_status`, tests, and
global registration. Out: Phase 3 (agent-driven discovery), `SubagentStop` gating
(main `Stop` only), and any change to the Phase 1 runner behavior.

## Naming defaults

`caps gate` (hook entrypoint), `bin/caps-stop-gate.sh` (wrapper), `caps install-hook` /
`uninstall-hook`, `caps/state.py` + `capability_state`. Open to change.
