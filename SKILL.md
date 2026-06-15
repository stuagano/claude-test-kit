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
