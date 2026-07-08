---
title: Merging a Feature Branch Across a Mid-Flight Repo-Wide Reformat
date: 2026-07-07
category: docs/solutions/workflow-issues
module: caps CLI (cli.py) + .ctk/ledger.json — git merge workflow
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - Merging a long-lived feature branch into main after main has adopted a repo-wide auto-formatter (e.g. ruff format) or linter/type-checker mid-flight
  - The feature branch and the upstream formatter-adoption commit both modify the same file in incompatible styles (pre-formatter vs. post-formatter)
  - A machine-generated proof/state file (e.g. a JSON ledger of timestamps/fingerprints) conflicts on merge with no semantic content difference between sides
  - You need to trust a hand-resolved merge conflict against a newly-adopted formatter/linter rather than just eyeballing it
tags: [git, merge-conflict, ruff, formatter-adoption, ledger, caps, ci-toolchain]
related_components: [tooling]
---

# Merging a Feature Branch Across a Mid-Flight Repo-Wide Reformat

## Context

A feature branch (`feat/caps-evidence-citation`, adding a `caps evidence <id>` CLI
command to this project's `caps` capability-verification tool) had modified
`caps/cli.py` twice while it was in flight — once to add the new `evidence`
subcommand, once to extract a shared `_require_capability` helper. Meanwhile,
`origin/main` moved on without it: two separately-merged PRs adopted a repo-wide
toolchain (`pyproject.toml`, ruff for lint+format, mypy, pre-commit, a CI workflow),
and one of those PRs ran `ruff format` across the entire codebase — including
`caps/cli.py`, the exact file the feature branch had also touched.

The divergence surfaced the moment a routine "merge back to main locally" finish-up
was attempted: local `main` had 4 unpushed commits (design spec, implementation plan,
ledger housekeeping) and `git pull` on `main` failed outright with "Need to specify
how to reconcile divergent branches" (no rebase/merge default configured). Inspecting
the divergence showed it wasn't a routine fast-forward — it included a whole-codebase
style/format commit touching the same file the feature branch had modified, which is a
real conflict risk, not a formality. This was surfaced to the user before doing
anything, with three options (merge origin/main into local main first, rebase the
feature branch onto origin/main, or stop and inspect manually); the user chose to
merge `origin/main` into local `main` first.

This doc captures the general pattern — merging old, in-flight work into a `main`
that adopted a formatter/linter mid-flight — and a specific reusable technique for
resolving conflicts in machine-generated proof/state files along the way.

## Guidance

**1. Treat a repo-wide format/lint-adoption commit in the divergence as a conflict
signal, not noise.** Before merging, look at what actually changed upstream
(`git log`, `git diff` on the divergent range) rather than assuming `git pull` will
just fast-forward. If a formatter or linter was newly adopted and ran across the
codebase, any branch with unmerged changes to the same files needs manual reconciliation
of the *code*, not a blind merge.

**2. Merge in stages, verifying at each stage, rather than one giant merge.**
The two-stage approach used here:

```bash
git merge origin/main                       # stage 1: absorb upstream toolchain changes
# resolve conflicts, run tests, re-run project's own proof/verification tooling
git commit                                   # separate housekeeping commit

git merge feat/caps-evidence-citation        # stage 2: bring in the feature branch
# resolve conflicts, run tests, re-run project's own proof/verification tooling
git commit
```

Each stage gets its own conflict resolution and its own verification pass, instead of
trying to reconcile both the toolchain adoption and the feature branch's changes in one
merge.

**3. For machine-generated proof/state files, resolve conflicts by taking the
whole side rather than hand-merging content — but only when there is zero
human-authored content in the file.** In this repo, `.ctk/ledger.json` is a
proof ledger recording timestamps and file fingerprints for each proven capability.
On the first merge it had one conflicted block; `git checkout --ours` on
`.ctk/ledger.json` was sufficient (both sides were independently re-run proofs of the same capabilities,
no semantic disagreement — just "whose timestamp is newer"). On the second merge it
had grown to 9 conflicted blocks. Resolving 9 blocks by hand is tedious and
error-prone, so instead a throwaway script mechanically kept only the `HEAD` half of
every conflict marker block:

```python
import re

with open(".ctk/ledger.json") as f:
    content = f.read()

# Pattern for a single conflict block:
# <<<<<<< HEAD
# ...head content...
# =======
# ...their content...
# >>>>>>> feat/caps-evidence-citation
pattern = re.compile(
    r"<<<<<<< HEAD\n(.*?)\n=======\n.*?\n>>>>>>> [^\n]*\n",
    re.DOTALL,
)
resolved = pattern.sub(lambda m: m.group(1) + "\n", content)

with open(".ctk/ledger.json", "w") as f:
    f.write(resolved)
```

After running it: parse the result as JSON to confirm it's still valid, and
specifically check that any genuinely-new (non-conflicting) entries the feature
branch had added are still present — in this case `evidence-prints-ledger-fields`,
which had auto-merged cleanly and was not part of any conflict block.

**The rule that makes this safe**: this "always take HEAD" resolution is only valid
when the entire file is machine-recorded state (timestamps, hashes, waiver reasons) —
never when the file contains prose or logic a human wrote. If in doubt, `git diff`
each conflicted region first to confirm both sides are just different snapshots of
the same kind of data, not divergent decisions.

**4. For real code conflicts caused by a reformat, resolve by hand — keep both
sides' intent, then let the formatter itself confirm the result.** In
`caps/cli.py`, the feature branch's new code (a new `_EVIDENCE_FIELD_ORDER` list
constant, `_evidence_report()` and `_format_evidence()` helpers, a
`_require_capability()` helper, and a new `evidence` argparse subparser with its
two arguments) needed to be inserted at points where ruff's repo-wide reformat had
changed the surrounding code's line-wrapping (a function signature collapsed from
two lines to one; an `add_argument()` call expanded from one line to a wrapped
multi-line call). The resolution kept 100% of the feature branch's logic but
manually reformatted the *new* code to match ruff's conventions (breaking the list
literal one-item-per-line, wrapping the `sub.add_parser(...)` call across lines)
instead of leaving it in the feature branch's original single-line style.

The verification step that turns "I think I formatted this correctly" into proof:

```bash
ruff format caps/cli.py --diff   # → "1 file already formatted" (zero diff)
ruff check caps/cli.py           # → "All checks passed!" (zero lint violations)
```

Zero diff from `ruff format --diff` means the hand-resolution already matches
exactly what the formatter would have produced — using the same tool that CI or a
pre-commit hook would otherwise use to flag it, rather than relying on manual
judgment about style conformance.

**5. Re-run the full test suite and the project's own verification tooling after
each merge stage, before committing.** Here: `./run_tests.sh unit` (171 passed
after stage 1, matching the pre-feature-branch baseline; 177 passed after stage 2,
including the feature branch's 6 new tests, no regressions) and
`caps verify --stale` (re-proves the project's own capabilities against the
newly-merged code) after both stages, each committed as part of or immediately
following its merge commit.

## Why This Matters

The naive shortcuts here are seductive because they make the conflict "go away"
without actually resolving anything:

- `git merge --strategy-option=ours` or blindly accepting "ours" for
  `caps/cli.py` would silently revert the repo-wide reformat, reintroducing the old
  style and almost certainly failing the newly-adopted `ruff format --check` gate
  the very next time CI or a pre-commit hook ran.
- Blindly accepting "theirs" for the whole file would silently discard the feature
  branch's new code — no error, no warning, just quietly missing functionality that
  looks merged but isn't there.

Both failure modes are invisible at merge time — `git` reports a clean merge either
way — and only surface later, either as a CI failure on an unrelated commit or as a
"where did my feature go" bug report. The cost of getting this wrong is deferred and
harder to diagnose than the conflict itself. Resolving conflicts by content (keep
both sides' actual intent) and then verifying against the *tool* that will be the
actual gate (`ruff format --diff`, `ruff check`) converts a judgment call into a
checked fact before the merge commit lands.

## When to Apply

- Merging a long-lived feature branch into a `main` that adopted a formatter,
  linter, or other repo-wide reformatting tool while the branch was in flight.
- Any merge where `git diff` on the divergent range shows a commit that reformatted
  files your branch also touched — treat this as a signal to inspect, not to
  fast-forward through.
- Conflicts in any file that is purely machine-generated proof/state data (ledgers,
  lockfiles-with-metadata, generated snapshots) where both sides are independent
  re-generations of the same kind of data with no human-authored content.
- After hand-resolving a conflict in a file governed by a formatter/linter, before
  committing the merge.

## Examples

**Ledger conflict, single block (safe to take whole-file):**
```bash
git merge origin/main
# CONFLICT (content): Merge conflict in .ctk/ledger.json
git checkout --ours .ctk/ledger.json
git add .ctk/ledger.json
```
Valid because the file's entire content was proof-state (timestamps, fingerprints)
with no partial/prose content mixed in.

**Ledger conflict, many blocks (script instead of hand-resolving each):**
Use the regex `<<<<<<< HEAD\n(.*?)\n=======\n.*?\n>>>>>>> [^\n]*\n` matched with
`re.DOTALL`, substituting each match with just its captured HEAD-side group. Confirm
afterward: (a) the result parses as valid JSON/whatever the file format is, and (b)
any new entries introduced by the non-HEAD side that were NOT part of a conflict
block (i.e., auto-merged cleanly) are still present — those are real additions, not
proof-state noise, and must survive.

**Code conflict caused by reformat (hand-resolve, then verify with the tool):**
```bash
# after manually merging the new helper functions/constants into the
# now-differently-wrapped surrounding code:
ruff format caps/cli.py --diff   # expect: "1 file already formatted"
ruff check caps/cli.py           # expect: "All checks passed!"
./run_tests.sh unit              # expect: same or higher pass count than baseline
caps verify --stale              # expect: all capabilities re-prove clean
```
If `ruff format --diff` shows any diff, the hand-resolution doesn't match the
adopted style yet — run `ruff format` on `caps/cli.py` to let the tool fix it
automatically, then re-check that the feature branch's logic is still intact.

## Related

- `.ctk/ledger.json` — the project's own capability-verification proof ledger
  (see `caps verify` / `caps status` in this repo's `CLAUDE.md`).
- `caps/cli.py` — the CLI module both the toolchain-adoption reformat and the
  feature branch modified.
