"""UNIT tests for ctk.docs_direction — direction review with a stubbed runner."""

import json

import pytest

pytestmark = pytest.mark.unit

from ctk.docs_direction import (
    DirectionVerdict,
    format_verdicts,
    review_doc_direction,
)


def _runner_returning(payload):
    return lambda prompt: json.dumps(payload)


def test_current_verdict_parsed(workspace):
    workspace.write("README.md", "# Project\nCurrent truth.\n")
    workspace.write("docs/a.md", "# A\nStill accurate.\n")
    runner = _runner_returning(
        {"verdict": "current", "rationale": "matches", "doc_evidence": [], "source_evidence": []}
    )
    verdicts = review_doc_direction(["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert len(verdicts) == 1 and verdicts[0].verdict == "current"


def test_format_verdicts_lists_docs():
    v = DirectionVerdict("docs/a.md", "overtaken", "pivoted", ["x"], ["y"])
    assert "docs/a.md" in format_verdicts([v])


def test_overtaken_with_real_evidence_is_kept(workspace):
    workspace.write("README.md", "# Project\nThe default window is 48h now.\n")
    workspace.write("docs/a.md", "# A\nThe default window is 24h.\n")
    runner = _runner_returning(
        {
            "verdict": "overtaken",
            "rationale": "window changed",
            "doc_evidence": ["The default window is 24h."],
            "source_evidence": ["The default window is 48h now."],
        }
    )
    verdicts = review_doc_direction(["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert verdicts[0].verdict == "overtaken"


def test_overtaken_with_fake_evidence_is_discarded(workspace):
    workspace.write("README.md", "# Project\nNothing relevant here.\n")
    workspace.write("docs/a.md", "# A\nReal content.\n")
    runner = _runner_returning(
        {
            "verdict": "overtaken",
            "rationale": "hallucinated",
            "doc_evidence": ["a line that is not in the doc"],
            "source_evidence": ["also not present anywhere"],
        }
    )
    verdicts = review_doc_direction(["docs/a.md"], repo_root=str(workspace.root), runner=runner)
    assert verdicts[0].verdict == "uncertain"
    assert "evidence" in verdicts[0].rationale.lower()


def test_default_runner_raises_when_cli_missing(monkeypatch):
    import ctk.docs_direction as dd

    monkeypatch.setattr(dd.shutil, "which", lambda _name: None)
    with pytest.raises(dd.ClaudeUnavailable):
        dd._claude_cli_runner("any prompt")


def test_default_runner_treats_timeout_as_unavailable(monkeypatch):
    import ctk.docs_direction as dd

    monkeypatch.setattr(dd.shutil, "which", lambda _name: "/usr/bin/claude")

    def _timeout(*_args, **_kwargs):
        raise dd.subprocess.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(dd.subprocess, "run", _timeout)
    with pytest.raises(dd.ClaudeUnavailable):
        dd._claude_cli_runner("any prompt")
