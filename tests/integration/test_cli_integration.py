"""
INTEGRATION: run the real scripts as subprocesses and verify real side effects.
This is the kit's anti-silent-failure flow end to end, and the template to copy
for your own scripts/agents.
"""

import json
import sys

import pytest

pytestmark = pytest.mark.integration

from ctk import run, expect, Artifact, verify, claim_vs_reality, VerificationError

GOOD = "examples/word_count.py"
BUGGY = "examples/buggy_word_count.py"


def test_good_tool_full_flow(workspace, run_started_at):
    infile = workspace.write("input.txt", "the quick brown fox jumps")
    outfile = workspace.path("result.json")

    r = run([sys.executable, GOOD, infile, "--out", outfile])
    r.ok().no_stderr_errors()
    expect(r.stdout).nonempty().matches(r"Processed \d+ words").verify()
    verify(Artifact(outfile, min_bytes=2, is_json=True,
                    json_keys=["ok", "words", "chars"], newer_than=run_started_at))
    assert json.loads(workspace.read("result.json"))["words"] == 5


def test_good_tool_error_path_is_loud(workspace):
    outfile = workspace.path("result.json")
    r = run([sys.executable, GOOD, "does_not_exist.txt", "--out", outfile])
    r.failed()
    assert "cannot read" in r.stderr


def test_buggy_tool_is_caught_despite_exit_zero(workspace, run_started_at):
    """The buggy tool exits 0 and lies; claim_vs_reality unmasks it."""
    infile = workspace.write("input.txt", "real content that should be counted")
    outfile = workspace.path("bug.json")
    r = run([sys.executable, BUGGY, infile, "--out", outfile])

    r.ok()  # it really does exit 0
    with pytest.raises(VerificationError, match="SILENT FAILURE"):
        claim_vs_reality(
            claimed_success=(r.returncode == 0),
            verifier=lambda: verify(Artifact(outfile, min_bytes=2, is_json=True,
                                              newer_than=run_started_at)),
            claim_label="buggy_word_count",
        )
