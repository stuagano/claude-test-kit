---
name: ctk
description: >-
  Python test harness (claude-test-kit) that catches work which claims success
  but didn't actually do the thing — exit-0-but-wrong-output, says-done-isn't,
  swallowed exceptions, and missing output validation. Use this whenever writing
  or strengthening tests for Python tools, CLIs, scripts, data pipelines, or
  agent/LLM steps — ESPECIALLY when verifying that something "actually works"
  rather than just runs. Reach for it on phrases like "write tests", "verify
  this works", "make sure it actually did X", "test this CLI/script", "test my
  agent", "catch silent failures", "the run said done but the file is empty", or
  any time you're about to trust an exit code or a printed "success" message.
  The framework code lives in this skill — copy ctk/ + conftest.py into the
  target project to use it.
  ALSO includes the `caps` capability layer — a declared `capabilities.yaml` of
  the things a project promises ("writes to the DB", "the endpoint works", "the
  deploy is live"), each proven by a real read-after-write check. IMPORTANT
  in-band behavior: in ANY project that has a `capabilities.yaml`, run
  `python -m caps verify` (or `status`) and do NOT call work "done" until it is
  green and fresh; and whenever you do capability-shaped work (write to a DB,
  deploy, create a table/file/endpoint) in a project with no matching check,
  PROPOSE adding one to the manifest. Reach for this on "capability", "prove it
  works", "read-after-write", "make sure we can actually write to X", "verify
  the deploy", or any claim that a project "can do" something.
---

# ctk — the anti-silent-failure test harness

## What this is and why it exists

The dangerous failure in automated work is rarely a crash. It's a process that
exits `0`, prints `Processed 1,000 rows`, and leaves an empty file behind.
Standard testing asks *"did it run?"*. `ctk` forces the question that actually
matters: **"did it produce the correct result?"** — by asserting on real side
effects (files, rows, state), not on the claim.

Use it whenever the cost of a confident-but-wrong run is high: testing CLIs and
scripts, data jobs, and especially agent/LLM steps where the model reports
success but the artifact is wrong or stale.

## Where the code is

This skill bundles the framework. To use it in a project, copy the two pieces in:

```bash
cp -r <this-skill-dir>/ctk <target-project>/
cp <this-skill-dir>/conftest.py <target-project>/   # the error-log guard + workspace fixture
```

`pytest.ini` in this repo shows the marker/pythonpath setup to mirror. There is
nothing to `pip install` — `ctk` is plain pytest plus stdlib.

## The five tools (reach for the one that fits)

1. **`run(...)` — strict subprocess runner.** Exit 0 is not proof. Assert on
   everything: `r.ok()`, `r.no_stderr_errors()` (catches tracebacks even when
   exit==0), `r.out_matches(...)`, `r.json()`.

2. **`expect(...)` — output contracts.** Declare what valid output looks like;
   every check runs so you see all failures at once. End the chain with
   `.verify()` — that's what raises:
   `expect(out).nonempty().matches(r"\d+ rows").is_json().has_keys("rows","ok").verify()`

3. **`Artifact` + `verify(...)` — agent verification (the core idea).** Declare
   the concrete outputs that must exist and check them, including `newer_than=`
   freshness so a leftover file from a prior run can't masquerade as new output.

4. **`claim_vs_reality(...)` — the silent-failure tripwire.** Reconcile what was
   reported against what's true. Pass the reported success signal and an
   independent verifier; it raises `SILENT FAILURE` when the claim and reality
   disagree. This is the exact "said done, isn't" check.

5. **`find_swallowed_exceptions(...)` — AST scan.** Static check for
   `except: pass`, `except Exception: pass`, and except-blocks that only log and
   never re-raise. Make it a test. Backed at runtime by the autouse
   `fail_on_error_log` fixture in `conftest.py`, which fails any test whose code
   logged at ERROR/CRITICAL.

## Testing agents / prompts — the key discipline

LLM output isn't deterministic, so never assert on exact wording. Assert on
**verifiable effects and invariants** instead:

- **Effects, not prose** — after the step runs, did the file/row/state it
  promised actually change? `verify(Artifact(...))` against the real artifact.
- **Contracts, not snapshots** — check structure with `expect(...)`: parses as
  JSON, has keys x/y, mentions every input ticker, length within range.
- **Reconcile the claim** — capture the step's own success/failure signal and
  run `claim_vs_reality` against an independent verifier.
- **Bound the loop** — put the whole verification in `run_tests.sh` and gate the
  iterate loop on its exit code. "Done" = the suite is green, not the model
  saying so.

## Capability verification — the `caps` layer (stays-in-band checks)

Beyond writing tests, `caps` proves the capabilities a project *claims*. A
`capabilities.yaml` declares each promise; each is proven by a real check built
on `claim_vs_reality`. This is the layer that catches "we said we can write to
the DB" when we actually can't.

**Do this automatically — don't wait to be asked:**

1. **If the project has a `capabilities.yaml`** (check the repo root): run
   `python -m caps status` to see state, and `python -m caps verify` before
   treating work as done. **"Done" means every capability is green and fresh —
   not that the code ran.** If `verify` is red/stale, that blocks "done"; fix it
   or, if it genuinely can't be proven right now (offline, no infra), record an
   explicit waiver: `python -m caps ack <id> --reason "..."`.
2. **If you do capability-shaped work and there's no matching check** — anything
   that writes to a DB, deploys, creates a table/file/endpoint, etc. — **propose
   a capability** rather than silently moving on. Surface a concrete proposal
   (id, given/when/then, tier, deps, check), and on the user's **explicit yes**
   wire it in with `caps add` — never hand-edit `capabilities.yaml`:

   ```
   python -m caps add --id <id> --tier <cheap|live> \
     --description "..." --given "..." --when "..." --then "..." \
     --deps <glob> [--deps <glob> ...] \
     --check checks/test_<id>.py::test_<id>      # or --shell "./prove.sh"
   ```

   `caps add` creates the entry as **never-proven** and scaffolds a *failing*
   check stub — it cannot fake a pass. Then implement the scaffolded check body
   (the real write → readback → teardown) and run
   `python -m caps verify --capability <id>` to actually prove it. You are
   advisory: never add a capability without the user's explicit yes.

**Manifest shape:**

```yaml
capabilities:
  - id: writes-to-lakebase
    description: the ingest job writes order rows and they read back
    given: a reachable Lakebase instance
    when:  the ingest job runs
    then:  the written rows are readable back with matching ids
    tier:  live            # cheap (fingerprint freshness) | live (time-window, default 24h)
    deps:  [ingest.py]     # changing these invalidates the proof
    check: checks/test_lakebase_write.py::test_write_readback   # or { shell: "./prove.sh" }
```

**Using it in a project:** copy `caps/` in alongside `ctk/` (and ensure
`PyYAML` is installed), or run from this kit with the kit on `PYTHONPATH`:
`PYTHONPATH=<this-skill-dir> python -m caps verify`.

> Note: enforcement is currently a discipline this skill reminds you of. A
> `Stop` hook that blocks "done" automatically (so it can't be skipped) is the
> planned Phase 2 — until it lands, treat the steps above as mandatory whenever
> a `capabilities.yaml` is present.

## Unit vs. integration

Both run on pytest; the difference is only how fixtures wire dependencies, split
by marker. **Unit** (`@pytest.mark.unit`): isolate the unit, mock the
boundaries, runs in milliseconds — gate every save on it. **Integration**
(`@pytest.mark.integration`): real deps (sqlite on disk, a live HTTP server,
real subprocesses) set up and torn down by fixtures.

```
pytest -m unit            # fast inner-loop gate
pytest -m integration     # real deps
pytest -m "not slow"      # everything quick
```

## Going deeper

`README.md` in this skill has the full worked examples, the integration fixture
patterns (including swapping sqlite for a real Postgres testcontainer), and the
complete file layout. Start from `tests/integration/test_cli_integration.py`
(the anti-silent-failure flow) and `tests/unit/test_api_client_unit.py`
(mock-the-boundary) — they're the templates to copy for new code.
