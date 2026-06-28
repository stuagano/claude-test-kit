"""Capability check for `docs-direction-current` (live, LLM-first).

Runs the evidence-verified direction review over the project's docs. Fail-open:
if the `claude` CLI is unavailable, the check skips (the live capability is left
un-proven rather than falsely proven — re-prove when the CLI is present, or
`caps ack` with a reason).
"""

import glob
import os

import pytest

from ctk import (
    ClaudeUnavailable,
    format_verdicts,
    review_doc_direction,
)


def _select_direction_docs():
    # Review narrative docs; skip the archival spec/plan tree by default.
    docs = ["README.md", "SKILL.md", "CLAUDE.md"]
    docs += [
        d
        for d in glob.glob("docs/**/*.md", recursive=True)
        if not d.startswith("docs/superpowers/")
    ]
    return [d for d in docs if os.path.exists(d)]


def test_no_overtaken_docs():
    try:
        verdicts = review_doc_direction(_select_direction_docs())
    except ClaudeUnavailable as e:
        pytest.skip(f"claude CLI unavailable — direction review skipped: {e}")
    overtaken = [v for v in verdicts if v.verdict == "overtaken"]
    assert overtaken == [], "\n" + format_verdicts(overtaken)
