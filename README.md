# claude-test-kit (ctk)

A small Python testing framework built around one problem: **work that claims success but didn't actually do the thing.** It's pytest underneath, plus a thin layer of helpers that make silent failure impossible to ignore.

## Why this exists

The usual failure isn't a crash — it's a process that exits 0, prints "Processed", and leaves you with an empty file. Standard testing checks "did it run?". This kit checks **"did it actually produce the correct result?"** and forces you to assert on reality, not on the claim.

It targets four specific failure modes:

| Failure mode | What catches it |
|---|---|
| **Says done, isn't** | `ctk.verify` — claim-vs-reality checks against real side effects |
| **No output validation** | `ctk.expect` — declarative output contracts |
| **Swallowed exceptions** | `ctk.find_swallowed_exceptions` (AST scan) + the error-log guard |
| **Exit 0 but wrong output** | `ctk.run` — a strict runner that asserts exit code *and* output |

## Setup

```bash
cd claude-test-kit
./run_tests.sh            # installs pytest, runs the whole suite
./run_tests.sh unit       # fast isolated tests only — the inner-loop gate
./run_tests.sh integration
./run_tests.sh cov        # with coverage
```

Nothing to install as a package — `pytest.ini` puts `ctk` on the path. To use it in your own project, copy the `ctk/` folder and `conftest.py` into your repo.

## Unit vs. integration

Both kinds of test run on pytest; the difference is purely how the fixtures wire dependencies, and they're separated by marker so you can run them independently.

**Unit** (`tests/unit/`, `@pytest.mark.unit`) — isolate the unit, mock the boundaries. No DB, no network, no subprocess. Runs in milliseconds; gate every save on it. See `test_api_client_unit.py` — it `monkeypatch`es the network so only the parsing logic is under test.

**Integration** (`tests/integration/`, `@pytest.mark.integration`) — real dependencies, set up and torn down by fixtures:

- `test_store_integration.py` — a **real sqlite database** on disk; the read-after-write round-trip with no mocks.
- `test_http_integration.py` — a **real HTTP server** on a real port (the `live_server` fixture in `tests/integration/conftest.py`).
- `test_cli_integration.py` — runs the **real scripts as subprocesses** and verifies side effects with the ctk flow.

The fixtures use stdlib so they run anywhere. In a real project, swap them for the production-grade equivalents — same fixture *shape* (yield a target, clean up after):

```python
# tests/integration/conftest.py — real Postgres instead of sqlite
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()
```

Typical workflow: `./run_tests.sh unit` constantly while coding, full `./run_tests.sh` (or `-m "not slow"`) in CI.

```
pytest -m unit                 # fast loop
pytest -m integration          # real deps
pytest -m "not slow"           # everything quick
pytest -m "unit or integration"
```

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
The core idea. Declare the concrete outputs that must exist, then check them — including a freshness check so a leftover file from a previous run can't masquerade as new output.

```python
from ctk import Artifact, verify
verify(
    Artifact("result.json", min_bytes=2, is_json=True, json_keys=["rows"], newer_than=started_at),
    Artifact("report.md", min_bytes=200, must_contain="## Summary"),
)
```

### 4. Claim vs. reality — `claim_vs_reality(...)`
Reconcile what was reported against what's true. This is the exact "Claude says done, isn't" check.

```python
from ctk import claim_vs_reality, verify, Artifact
claim_vs_reality(
    claimed_success=(r.returncode == 0),                       # what it reported
    verifier=lambda: verify(Artifact("out.json", is_json=True)),# what's actually true
    claim_label="my_task",
)
# raises "SILENT FAILURE" if it claimed success but reality is wrong.
```

### 5. Swallowed-exception scanner — `find_swallowed_exceptions(...)`
Static AST scan for `except: pass`, `except Exception: pass`, and except-blocks that only log and never re-raise. Make it a test:

```python
from ctk import find_swallowed_exceptions
def test_no_swallowed_exceptions():
    assert find_swallowed_exceptions("my_pkg/") == []
```

Plus a runtime net in `conftest.py`: the autouse `fail_on_error_log` fixture **fails any test whose code logged at ERROR/CRITICAL**, even if the exception was caught. Opt out per-test with `@pytest.mark.allow_error_logs`.

## Testing agents / prompts

LLM steps aren't deterministic, so don't assert on exact wording. Assert on **verifiable effects and invariants**:

- **Effects, not prose.** After the agent runs, did the file/row/state it promised actually change? Use `verify(Artifact(...))` against the real artifact.
- **Contracts, not snapshots.** Check structure: "output parses as JSON", "has keys x/y", "mentions every input ticker", "length within range" — via `expect(...)`.
- **Reconcile the claim.** Capture the agent's own "success/failure" signal and run `claim_vs_reality` against an independent verifier. This is your tripwire for confident-but-wrong runs.
- **Bound the loop.** Put the whole verification in `run_tests.sh` and make your iterate loop gate on its exit code. "Done" = the suite is green, not the model saying so.
- **Golden checks for stable bits.** For deterministic sub-steps (parsing, formatting), keep a fixed expected output and `assert_eq`.

A typical agent test:

```python
def test_agent_produced_valid_report(workspace, run_started_at):
    r = run([...invoke your agent..., "--out", workspace.path("report.json")])
    claim_vs_reality(
        claimed_success=(r.returncode == 0),
        verifier=lambda: verify(Artifact(
            workspace.path("report.json"),
            is_json=True, json_keys=["summary", "items"],
            newer_than=run_started_at,
        )),
        claim_label="report agent",
    )
```

## Layout

```
claude-test-kit/
├── ctk/                         # the framework
│   ├── runners.py               # run() strict subprocess runner
│   ├── contracts.py             # expect() output contracts
│   ├── assertions.py            # must(), assert_file(), assert_eq()
│   ├── verify.py                # Artifact, verify(), claim_vs_reality(), Checklist
│   └── lint.py                  # find_swallowed_exceptions() AST scan
├── conftest.py                  # workspace fixture + error-log guard (shared)
├── examples/                    # realistic targets to test
│   ├── word_count.py            # well-behaved CLI tool
│   ├── buggy_word_count.py      # fails silently (for the demo tests)
│   ├── store.py                 # sqlite-backed store (unit + integration target)
│   └── api_client.py            # HTTP client (unit + integration target)
├── tests/
│   ├── unit/                    # @pytest.mark.unit — mocked, fast
│   │   ├── test_store_unit.py
│   │   ├── test_api_client_unit.py   # monkeypatches the network
│   │   ├── test_ctk_unit.py          # tests the kit itself
│   │   └── test_error_log_guard.py
│   └── integration/             # @pytest.mark.integration — real deps
│       ├── conftest.py          # db_path + live_server fixtures
│       ├── test_store_integration.py # real sqlite read-after-write
│       ├── test_http_integration.py  # real HTTP server
│       └── test_cli_integration.py   # runs real scripts as subprocesses
├── pytest.ini                   # markers, pythonpath, --strict-markers
├── requirements.txt
└── run_tests.sh
```

Start with `tests/integration/test_cli_integration.py` (the anti-silent-failure flow) and `tests/unit/test_api_client_unit.py` (the mock-the-boundary pattern) — they're the templates for your own code.
