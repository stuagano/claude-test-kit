"""
UNIT tests for the kit itself — pure, no subprocess. These guard the guards:
if a detector stops firing, the kit would give false confidence.
"""

import pytest

pytestmark = pytest.mark.unit

from ctk import (
    expect, ContractError,
    Artifact, verify, claim_vs_reality, VerificationError,
    find_swallowed_exceptions, assert_file,
)
from ctk.assertions import CheckError


# ---- contracts ----
def test_contract_passes_on_valid_output():
    expect('{"rows": 3, "ok": true}').nonempty().is_json().has_keys("rows", "ok").verify()


def test_contract_collects_all_failures():
    with pytest.raises(ContractError) as ei:
        expect("").nonempty().matches(r"\d+").verify()
    # both failures reported together, not just the first
    msg = str(ei.value)
    assert "non-empty" in msg and "match" in msg


# ---- verify / artifacts ----
def test_verify_passes_on_good_file(workspace):
    p = workspace.write("ok.json", '{"x": 1}')
    verify(Artifact(p, min_bytes=2, is_json=True, json_keys=["x"]))


def test_verify_flags_empty_file(workspace):
    p = workspace.write("empty.json", "")
    with pytest.raises(VerificationError):
        verify(Artifact(p, min_bytes=2, is_json=True))


def test_claim_vs_reality_flags_silent_failure(workspace):
    p = workspace.write("empty.json", "")
    with pytest.raises(VerificationError, match="SILENT FAILURE"):
        claim_vs_reality(True, lambda: verify(Artifact(p, min_bytes=2)), claim_label="t")


def test_claim_vs_reality_flags_false_alarm(workspace):
    p = workspace.write("good.json", '{"x":1}')
    with pytest.raises(VerificationError, match="FALSE ALARM"):
        claim_vs_reality(False, lambda: verify(Artifact(p, min_bytes=2)), claim_label="t")


def test_assert_file_catches_too_small(workspace):
    p = workspace.write("tiny.txt", "")
    with pytest.raises(CheckError):
        assert_file(p, min_bytes=1)


# ---- lint scanner ----
def test_lint_clean_on_kit():
    assert find_swallowed_exceptions("ctk") == []


def test_lint_flags_swallowed(tmp_path):
    src = "try:\n    x()\nexcept Exception:\n    pass\n"
    f = tmp_path / "bad.py"
    f.write_text(src)
    found = find_swallowed_exceptions(str(f))
    assert found and found[0].kind == "broad-pass"
