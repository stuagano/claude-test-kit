# `caps init` — Drop-In Installer Design

**Date:** 2026-06-16
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill
**Builds on:** Phases 1–3 (runner, Stop-hook enforcement, discovery), all shipped.

## Problem

Adopting the kit in another project is currently manual and error-prone: copy `ctk/`
+ `caps/` + `conftest.py` + `bin/`, install PyYAML, mirror `pytest.ini`, create a
manifest — while *not* copying the kit's own `capabilities.yaml`, `.ctk/`, `examples/`,
or `tests/`. That's ~5 "do" and ~4 "don't" steps an agent must remember. `caps init`
replaces it with one verb: run it in a target project and the framework is in place,
self-contained, ready for `caps add`.

## Distribution model: vendor (decided)

`caps init` **vendors** — it copies the framework files into the target so the project
is self-contained (committable, works in CI and on any machine, no dependency on where
the kit lives). This is the right fit for "drop into an arbitrary repo," especially for
an AI agent that can't assume the kit is on `PYTHONPATH` or pip-installed. The cost is
drift from the kit; a future `caps update` (out of scope here) re-vendors and shows
what changed. Rejected: pip-install (adds packaging + per-env install friction) and
symlink (non-portable — breaks on another machine / in CI).

## Command

```
caps init [--target <dir>] [--force] [--install-deps]
```

- Run from the kit; the framework **source** is resolved from the caps package
  location: `KIT = Path(caps.__file__).parent.parent`. Whatever state the kit is in is
  what gets vendored.
- `--target` defaults to the current working directory.
- `--force` controls **only** re-overwriting the vendored framework dirs (see Safety).
- `--install-deps` pip-installs PyYAML into the active environment; otherwise init only
  prints the one-line install.

`--with-hook` is intentionally **NOT** in v1 — see "Hook setup is deferred".

## What it does (each step independent + skip-if-exists)

1. **Vendor the framework** — copy `ctk/`, `caps/`, `bin/` from `KIT` into the target,
   **excluding `__pycache__/` and `*.pyc`** (never vendor build artifacts — the tool
   whose job is clean drop-in must not drag cruft). If a target dir already exists,
   skip it unless `--force` (with `--force`, overwrite it — the update path).
2. **`conftest.py`** — if the target has none, copy the kit's (the `workspace` fixture
   + the autouse `fail_on_error_log` guard). If it already has one, **do not clobber
   it**; instead print a LOUD warning + the exact two fixtures to add, stating the
   consequence plainly: *"you kept your conftest; until you add these, the error-log
   guard is OFF and any vendored check using the `workspace` fixture will error."*
3. **pytest config** — if no `pytest.ini`/pytest config exists, write a minimal
   `pytest.ini` (`pythonpath = .` and the `unit`/`integration`/`slow`/
   `allow_error_logs` markers). If one exists, print the required `pythonpath`/markers
   to merge.
4. **Starter `capabilities.yaml`** — if absent, write one with a single *commented*
   example entry and create `checks/`. If it exists, skip + note (re-running init must
   never overwrite the user's manifest).
5. **PyYAML** — print the one-line install. With `--install-deps`, pip-install it (the
   only env-mutating action, and only on explicit opt-in).
6. **`.gitignore`** — append `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.bak.*` if
   missing. **Never** touch `.ctk/` — the ledger is meant to be committed.
7. Print **next steps**: `caps add ...`, `caps verify`, and the manual hook-setup note.

## Safety / idempotency (pinned — the plan must not guess)

- **Each step is independently skip-if-exists.** `init` is safe to re-run; it repairs
  whatever's missing (e.g. a deleted `pytest.ini`) without `--force`.
- **`--force` scopes to the vendored framework only** (`ctk/`, `caps/`, `bin/`). It
  never overwrites `capabilities.yaml`, `conftest.py`, or an existing pytest config —
  those are always skip-if-exists regardless of `--force`.
- `init` only **adds**; it performs no destructive edits to user files.

## Hook setup is deferred (and why)

`--with-hook` cannot be made honest under the vendor model in v1:

- The wrapper resolves `KIT="$(dirname BASH_SOURCE)/.."` and runs
  `$KIT/.venv/bin/python`, failing **open** if absent. Vendored to
  `<target>/bin/caps-stop-gate.sh`, it would look for `<target>/.venv/bin/python`,
  which most target repos won't have (poetry/conda/system Python, or a venv without
  PyYAML) → hook installs clean, enforces nothing.
- `install_hook`'s default command derives from `caps.__file__` — during `init` that's
  the **kit's** caps, so it would register the kit's wrapper pointing at the kit,
  silently re-coupling the "self-contained" target back to the kit's location and
  `.venv`. That defeats vendoring.

So v1 **vendors the wrapper file** but does not install the hook. `init` prints how to
enable it once the target's interpreter is known, e.g.:
> To enforce on every turn: ensure a Python with PyYAML for this project, then
> `CAPS_GATE_PYTHON=/path/to/python python -m caps install-hook` (or create a
> `.venv` here so the wrapper's default resolves).

**Named future work:** *target-aware hook install* — `install-hook` (or a future
`init --with-hook`) discovers the target's Python (the one with PyYAML), bakes it into
the registered command, and points at `<target>/bin`. Until then, hook setup in a
vendored project is a documented manual step.

## Source resolution

The framework copied in is the kit's current `ctk/`, `caps/`, `bin/`, and
`conftest.py`, located via `Path(caps.__file__).parent.parent`. No network, no
packaging — whatever the kit contains is what the target gets.

## Testing (dogfood ctk)

- **Init into an empty temp dir:** `ctk/`, `caps/`, `bin/` present (and **no
  `__pycache__`/`.pyc`** vendored); `conftest.py`, `pytest.ini` written; starter
  `capabilities.yaml` + `checks/` created; `.gitignore` updated. Then
  `python -m caps status` runs in that dir and reports the (commented-out → empty)
  manifest cleanly.
- **Re-run without `--force`:** vendored dirs are left as-is; a deliberately-deleted
  `pytest.ini` is recreated (proves step-independence); existing `capabilities.yaml`
  is untouched.
- **`--force`:** vendored `caps/` is overwritten; `capabilities.yaml`/`conftest.py`
  still skipped.
- **Pre-existing `conftest.py`:** not clobbered; the warning + fixtures are printed
  (assert the loud-consequence wording is emitted).
- **Pre-existing `pytest.ini` / `capabilities.yaml`:** skipped with guidance.
- **`--install-deps`:** (mock/guard the pip call) — without it, init never mutates the
  env.
- **End-to-end:** after `init` in a temp project, `caps add ... && caps verify` works
  against the vendored framework (no `PYTHONPATH` gymnastics needed because the
  vendored `pytest.ini` sets `pythonpath = .`).

## Scope

In: the `caps init` command + tests. Out: `--with-hook` / target-aware hook install,
`caps update` (re-vendor + diff), pip packaging, and shipping the conftest fixtures as
an importable pytest plugin (the clean long-term fix for the conftest collision — worth
doing, but a separate change).

## Naming defaults

`caps init`, flags `--target`/`--force`/`--install-deps`. Starter manifest +
`checks/`. Open to change.
