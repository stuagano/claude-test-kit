# claude-test-kit (ctk + caps)

Two layers, one problem: **work that claims success but didn't actually do the thing.**

- **`ctk`** — pytest helpers that make silent failure impossible to ignore (the primitives).
- **`caps`** — declare the capabilities your project *promises* and prove them against reality, enforced **in band** by a `Stop` hook so "done" can't be faked. Built on `ctk`.

The usual failure isn't a crash — it's a process that exits `0`, prints `Processed 1,000 rows`, and leaves an empty file. Standard testing checks *"did it run?"*. This kit checks **"did it actually produce the correct result?"** — and `caps` goes further: it makes the agent (or CI, or you) *prove the project's claims* before finishing.

It targets four failure modes:

| Failure mode | What catches it |
|---|---|
| **Says done, isn't** | `ctk.verify` / `caps` — claim-vs-reality checks against real side effects |
| **No output validation** | `ctk.expect` — declarative output contracts |
| **Swallowed exceptions** | `ctk.find_swallowed_exceptions` (AST scan) + the error-log guard |
| **Exit 0 but wrong output** | `ctk.run` — a strict runner that asserts exit code *and* output |

---

# Layer 1 — `ctk`, the anti-silent-failure primitives

## Setup

```bash
cd claude-test-kit
./run_tests.sh            # bootstraps a local .venv, installs deps, runs the whole suite
./run_tests.sh unit       # fast isolated tests only — the inner-loop gate
./run_tests.sh integration
./run_tests.sh cov        # with coverage
```

`pytest.ini` puts `ctk` and `caps` on the path; the only dependency is `PyYAML` (for `caps`).

## The five tools

### 1. Strict runner — `run(...)`
Exit code 0 is not proof. Assert on everything.
```python
from ctk import run
r = run(["python", "my_tool.py", "--out", "result.json"])
r.ok()                 # fails loudly (full stdout+stderr) if exit != 0
r.no_stderr_errors()   # fails if stderr printed a traceback even when exit==0
r.out_matches(r"Processed \d+ rows")
data = r.json()        # parse stdout as JSON or fail
```

### 2. Output contracts — `expect(...)`
Declare what valid output looks like. Every check runs; you get all failures at once.
```python
from ctk import expect
expect(output).nonempty().matches(r"\d+ rows").is_json().has_keys("rows", "ok").verify()
```
Always end the chain with `.verify()` — that's what raises.

### 3. Agent verification — `Artifact` + `verify(...)`
Declare the concrete outputs that must exist, then check them — including a freshness check so a leftover file from a previous run can't masquerade as new output.
```python
from ctk import Artifact, verify
verify(
    Artifact("result.json", min_bytes=2, is_json=True, json_keys=["rows"], newer_than=started_at),
    Artifact("report.md", min_bytes=200, must_contain="## Summary"),
)
```

### 4. Claim vs. reality — `claim_vs_reality(...)`
Reconcile what was reported against what's true — the exact "says done, isn't" check.
```python
from ctk import claim_vs_reality, verify, Artifact
claim_vs_reality(
    claimed_success=(r.returncode == 0),
    verifier=lambda: verify(Artifact("out.json", is_json=True)),
    claim_label="my_task",
)  # raises "SILENT FAILURE" if it claimed success but reality is wrong.
```

### 5. Swallowed-exception scanner — `find_swallowed_exceptions(...)`
Static AST scan for `except: pass`, `except Exception: pass`, and except-blocks that only log and never re-raise.
```python
from ctk import find_swallowed_exceptions
def test_no_swallowed_exceptions():
    assert find_swallowed_exceptions("my_pkg/") == []
```
Plus a runtime net in `conftest.py`: the autouse `fail_on_error_log` fixture **fails any test whose code logged at ERROR/CRITICAL**, even if the exception was caught. Opt out per-test with `@pytest.mark.allow_error_logs`.

## Unit vs. integration

Both run on pytest; the difference is only how fixtures wire dependencies, split by marker. **Unit** (`@pytest.mark.unit`): isolate the unit, mock the boundaries, runs in milliseconds. **Integration** (`@pytest.mark.integration`): real deps (sqlite on disk, a live HTTP server, real subprocesses) set up and torn down by fixtures.

```
pytest -m unit            # fast inner-loop gate
pytest -m integration     # real deps
pytest -m "not slow"      # everything quick
```

## Testing agents / prompts

LLM steps aren't deterministic, so don't assert on exact wording. Assert on **verifiable effects and invariants**: did the file/row/state actually change (`verify(Artifact(...))`); does the output satisfy a contract (`expect(...)`); reconcile the agent's own success signal with an independent verifier (`claim_vs_reality`); and gate your iterate loop on the suite's exit code — "done" = green, not the model saying so.

---

# Layer 2 — `caps`, capability verification

`ctk` proves a single run. `caps` proves the **capabilities a project promises** — "we can write to the DB", "the endpoint works", "the deploy is live" — and keeps those proofs honest over time and **in band** (a `Stop` hook the harness runs, so it can't be silently skipped).

## The manifest — `capabilities.yaml`

A declared, readable, executable contract. Gherkin vocabulary; the `check` is what actually runs.

```yaml
capabilities:
  - id: writes-to-lakebase
    description: the ingest job writes order rows and they read back
    given:  a reachable Lakebase instance
    when:   the ingest job runs
    then:   the written rows are readable back with matching ids
    tier:   live            # cheap (fingerprint freshness) | live (time-window, default 24h)
    deps:   [ingest.py]     # changing these invalidates the proof
    check:  checks/test_lakebase_write.py::test_write_readback   # or { shell: "./prove.sh" }
```

A `check` is a `ctk`-based pytest test (or a shell command, exit 0 = proven) implementing **write → readback → teardown**. The safety/isolation strategy lives *inside each check*.

## Commands

```bash
python -m caps status                    # read-only: proven / stale / failed / waived / never-proven
python -m caps verify                    # run checks, record proof; non-zero exit if any fail
python -m caps verify --capability <id>  # just one
python -m caps ack <id> --reason "..."   # time-boxed waiver when it genuinely can't be proven now
python -m caps add  ...                  # propose a new capability (see Discovery)
```

## Freshness & the ledger

Proof is recorded in `.ctk/ledger.json` (committed, so CI / another machine sees current state). **Freshness differs by tier** — and the distinction is deliberate:

- **cheap → `freshness: code`** (fingerprint of the check + its `deps`). Touch a dep → stale → re-prove. Honest for local/deterministic checks. (Build artifacts like `__pycache__/*.pyc` are ignored.)
- **live → `freshness: 24h`** (time window). A live capability can break with *zero code change* (revoked perms, deleted instance), so its proof *expires* by the clock.

## Enforcement — the `Stop` hook

`caps install-hook` registers a global `Stop` hook (backs up `settings.json` first). On every turn, in any project that has a `capabilities.yaml`, it blocks "done" when a capability is **never-proven, failed, or code-stale** — handing the reason back so it gets fixed. It is:

- **read-only & fast** — it reads the ledger and hashes deps; it never runs checks on a turn boundary, and short-circuits instantly (no Python) in projects with no manifest;
- **self-clearing** — blocks at most once per turn (`stop_hook_active`), never an infinite loop;
- **quiet on time-expiry** — a live capability past its window is a *note*, not a block (that's `status`/CI's job, not a per-turn nag);
- **fail-open** — any internal error allows the turn (with a visible note) rather than bricking it.

Remove with `python -m caps uninstall-hook`.

## Discovery — `caps add`

When capability-shaped work happens with no matching check, propose one and (on approval) wire it in with `caps add` — never hand-edit the manifest:

```bash
python -m caps add --id <id> --tier <cheap|live> \
  --description "..." --given "..." --when "..." --then "..." \
  --deps <glob> [--deps <glob> ...] \
  --check checks/test_<id>.py::test_<id>      # or --shell "./prove.sh"
```

It appends a validated entry (validating in memory first — a bad append never corrupts the manifest) and scaffolds a **failing** check stub. The capability is born **never-proven** and red until you write the real check — `caps add` can never fabricate a pass.

---

## Using it in your project

One command vendors the framework into any project so it's self-contained:

```bash
cd <your-project>
PYTHONPATH=<kit> python -m caps init      # <kit> = a checkout/copy of this repo
```

`init` copies `ctk/`, `caps/`, and `bin/` in (excluding build artifacts), adds a
`conftest.py` and `pytest.ini` if you don't have them, writes a starter
`capabilities.yaml` + `checks/`, and updates `.gitignore`. Every step is
skip-if-exists, so re-running it only repairs what's missing. It never overwrites
your `capabilities.yaml`, `conftest.py`, or pytest config; `--force` re-vendors
only `ctk/`/`caps/`/`bin/`. Pass `--install-deps` to pip-install PyYAML, or run the
printed one-liner yourself.

Then declare your own capabilities (`python -m caps add ...`). The Stop-hook gate
is not installed by `init` under vendoring — the wrapper is vendored at
`bin/caps-stop-gate.sh`, and `init` prints how to register it once this project has
a Python with PyYAML.

## Layout

```
claude-test-kit/
├── ctk/                    # Layer 1 — copy-in primitives
│   ├── runners.py contracts.py assertions.py verify.py lint.py logguard.py
├── caps/                   # Layer 2 — capability verification (uses ctk)
│   ├── manifest.py fingerprint.py ledger.py freshness.py state.py runner.py
│   ├── gate.py manifest_edit.py hookinstall.py backup.py cli.py __main__.py
├── bin/caps-stop-gate.sh   # the Stop-hook wrapper (registered by install-hook)
├── conftest.py             # workspace fixture + error-log guard (shared)
├── capabilities.yaml       # THIS kit's own capabilities (it dogfoods itself)
├── .ctk/ledger.json        # committed proof state
├── examples/               # demo targets for the kit's own tests (not for drop-in)
├── tests/                  # unit + integration tests of the kit itself
├── docs/superpowers/       # design specs + implementation plans (the build history)
├── pytest.ini  requirements.txt  run_tests.sh  SKILL.md
```

`SKILL.md` is the agent entry point — it tells Claude when to reach for `ctk`, when to run/propose `caps`, and the in-band behavior. Start reading the tests from `tests/integration/test_cli_integration.py` (the anti-silent-failure flow) and `tests/unit/test_caps_gate.py` (the gate decision).
