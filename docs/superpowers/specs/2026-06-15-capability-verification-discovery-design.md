# Capability Verification — Phase 3 (Agent-Driven Discovery) Design

**Date:** 2026-06-15
**Status:** Approved (design); pending implementation plan
**Repo:** `claude-test-kit` (`~/Documents/claude-test-kit`), globally wired as the `ctk` skill
**Builds on:** Phase 1 (runner) and Phase 2 (Stop-hook enforcement), both shipped to main.

## Problem

The manifest only enforces what's *declared*. Today declaring a capability is fully
manual — nothing helps notice that work *should* have a capability. Phase 3 closes
the loop: when capability-shaped work happens (writing to a DB, deploying, creating a
table/file/endpoint) with no matching check, the agent **proposes** one; on user
approval, a code-backed `caps add` safely appends it (as a red, never-proven entry)
and scaffolds a failing check stub. This makes the manifest self-populating while
keeping the user as the sole gatekeeper and never fabricating a proof.

## Scope

In: a `caps add` command (+ a check-stub scaffolder), a shared backup helper, and the
`ctk` SKILL.md discovery behavior, with tests. Out: any `PostToolUse` auto-nudge
(agent-spotted/advisory only for v1 — an auto-nudge is the kind of thing that gets
noisy; revisit only if spotting-from-context proves unreliable), and any change to the
gate/runner behavior.

## Components

### 1. `caps add` (new `caps/manifest_edit.py` + CLI subcommand)

Non-interactive, flag-driven, so the skill calls it with values the user already
approved:

```
caps add --id writes-to-lakebase --tier live \
  --description "the ingest job writes order rows and they read back" \
  --given "a reachable Lakebase instance" \
  --when  "the ingest job runs" \
  --then  "the written rows are readable back with matching ids" \
  --deps ingest.py --deps "lib/db/**" \
  --check checks/test_lakebase.py::test_write_readback   # OR: --shell "./prove.sh app"
```

Flags: `--id` (required), `--description/--given/--when/--then` (required), `--tier`
(`cheap`|`live`, required), `--deps` (repeatable; zero or more), and exactly one of
`--check` (pytest node) or `--shell` (shell command). `--manifest <path>` defaults to
`capabilities.yaml` in the cwd-resolved project (reuse `project.find_root`; if none,
default to `./capabilities.yaml`). `freshness` is intentionally omitted from the
written entry so the tier default applies (cheap=code, live=24h).

### 2. Validate-in-memory, then write (the safety-critical ordering)

`caps add` MUST NOT mutate `capabilities.yaml` on disk before the result is known
good. Required ordering:

1. If the manifest exists, read its text; else start from a header
   (`# capabilities ...\ncapabilities:\n`).
2. Build the **candidate full text** in memory: existing text + a formatted entry
   block appended under `capabilities:` (block style, 2-space indent).
3. Parse the candidate with `load_manifest` (operating on the candidate text, e.g.
   via a temp path or an in-memory variant).
4. **Accept only if both hold:** the candidate parses without `ManifestError`, AND
   the new `id` is present in the parsed result. The id-present assertion (not merely
   "is it valid YAML?") is what catches the silent-wrong-result cases:
   - empty / null / flow-style `capabilities:` (`capabilities: []`, `capabilities:`)
     where an appended block doesn't merge into the list;
   - indent drift where the block parses as a new top-level key, not a list item.
5. Reject a **duplicate id** (id already in the existing manifest) before step 2 with
   a clear error and non-zero exit.
6. Only after the candidate validates: **back up** the existing manifest (if any) via
   the shared backup helper, then write the candidate text to disk.

If validation fails, the on-disk manifest is left untouched and `caps add` exits
non-zero with a clear message (e.g., "could not append a valid entry; manifest
unchanged" — likely a non-block-style `capabilities:`).

### 3. Failing check stub scaffolder

When `--check path::test_name` names a pytest node whose **file does not exist**,
`caps add` creates it with a stub that fails on purpose:

```python
# Scaffolded by `caps add` — replace with a real write → readback → teardown check.
def test_name():
    raise NotImplementedError("implement the capability check for <id>")
```

Integrity rule: the stub MUST be red. A freshly added capability is honestly
*unproven*, so `caps verify` is failing until a real check is written — `caps add`
can never manufacture a passing proof. If the check file already exists, it is **not**
overwritten (don't clobber a real check). `--shell` checks are not scaffolded (the
command is user-supplied).

### 4. Shared backup helper

`hookinstall.py` already has a private `_backup`. Extract it to a shared module
(`caps/backup.py`, function `backup_file(path)` → writes `path.bak.<YYYYMMDD>` with a
`-2`, `-3` suffix on collision) and have both `hookinstall` and `manifest_edit` use
it. Consistent with the user's global file-safety rule and avoids duplication.

### 5. Discovery behavior (the `ctk` SKILL.md)

Enhance the existing capability section. Agent-spotted and **advisory** — the agent
never auto-adds; the user approves. When the agent does or sees capability-shaped work
(write to a DB, deploy, create a table/file/endpoint) with no matching capability:

1. Surface a concrete proposal: id, given/when/then, tier, deps, check approach.
2. On the user's **yes**, run `caps add ...` with those values.
3. Implement the scaffolded check body (the real write → readback → teardown).
4. Run `caps verify --capability <id>` to actually prove it.

The capability's lifecycle is therefore: proposed → `caps add` (red stub) → real
check written → verified green. It can never skip to "proven."

## Data flow

```
agent notices capability-shaped work, no matching entry
   │  proposes id/g/w/t/tier/deps/check  → user approves
   ▼
caps add  ──► build candidate text in memory ──► load_manifest(candidate)
                                                    │ valid AND new id present?
                                          no ───────┘ exit nonzero, disk untouched
                                          yes ─► backup capabilities.yaml ─► write
                                              ─► scaffold failing stub (if pytest file absent)
   ▼
agent writes the real check body ──► caps verify --capability <id> ──► green
```

## Error handling

- **Duplicate id** → exit non-zero, manifest untouched, clear message.
- **Candidate doesn't validate** (non-block-style/empty `capabilities:`, indent drift)
  → exit non-zero, manifest untouched, message pointing at likely cause.
- **Both/neither of `--check`/`--shell`** → argparse/usage error, exit non-zero.
- **Check file already exists** → append entry, do NOT scaffold/overwrite.
- **Manifest absent** → create with header + the entry (still validate-before-write).

## Testing (dogfood ctk)

- **Unit (`manifest_edit`):**
  - append yields an entry `load_manifest` accepts and that contains the new id;
  - duplicate id rejected, manifest unchanged;
  - `capabilities.yaml` created-with-header when absent;
  - existing comments and prior entries preserved after an add (assert a known
    comment and a prior id still present);
  - non-block-style `capabilities: []` → rejected, file unchanged (validate-before-write);
  - pytest stub scaffolded only when the file is absent; an existing check file is not
    overwritten;
  - `--shell` entry appended without scaffolding;
  - a backup file is written when the manifest already existed.
- **Integrity test (the crux):** after `caps add` with a scaffolded stub,
  `caps verify --capability <id>` exits **non-zero** and the capability's state is in
  `{fail, never-proven}` (the stub raises → pytest fail → `run_capability` → `"fail"`).
  Then replace the stub body with a passing check → `caps verify` exits 0 / state
  `proven`. This pins that `caps add` cannot fabricate a green even if the stub
  template changes later.
- **Integration:** `python -m caps add ...` as a subprocess, then `caps status` lists
  the new capability as never proven.

## Naming defaults

`caps add` (subcommand), `caps/manifest_edit.py`, `caps/backup.py` + `backup_file`.
Open to change.
