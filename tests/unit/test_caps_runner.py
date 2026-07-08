import pytest

from caps.manifest import Capability
from caps.runner import run_capability


def _cap(**kw):
    base = {
        "id": "c",
        "description": "d",
        "given": "g",
        "when": "w",
        "then": "t",
        "tier": "cheap",
        "deps": [],
        "freshness": "code",
        "check_kind": "shell",
        "check_target": "true",
    }
    base.update(kw)
    return Capability(**base)


@pytest.mark.unit
def test_shell_pass(tmp_path):
    result, detail, duration = run_capability(_cap(check_target="exit 0"), tmp_path)
    assert result == "pass"
    assert detail is None  # a pass records no failure detail
    assert isinstance(duration, float) and duration >= 0.0


@pytest.mark.unit
def test_shell_fail(tmp_path):
    result, _, _ = run_capability(_cap(check_target="exit 1"), tmp_path)
    assert result == "fail"


@pytest.mark.unit
def test_shell_error_convention_exit_3(tmp_path):
    # Exit 3 is the reserved "could not run / unreachable" signal.
    result, _, _ = run_capability(_cap(check_target="exit 3"), tmp_path)
    assert result == "error"


@pytest.mark.unit
def test_shell_fail_captures_detail(tmp_path):
    # The failing output must come back so the gate can show *why* without a re-run.
    result, detail, _ = run_capability(_cap(check_target="echo boom-marker >&2; exit 1"), tmp_path)
    assert result == "fail"
    assert detail and "boom-marker" in detail


@pytest.mark.unit
def test_pytest_pass(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    cap = _cap(check_kind="pytest", check_target="checks/test_ok.py::test_ok")
    result, detail, duration = run_capability(cap, tmp_path)
    assert result == "pass"
    assert detail is None
    assert duration >= 0.0


@pytest.mark.unit
def test_pytest_fail(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    cap = _cap(check_kind="pytest", check_target="checks/test_bad.py::test_bad")
    result, detail, _ = run_capability(cap, tmp_path)
    assert result == "fail"
    assert detail and "test_bad" in detail  # the failing node name is surfaced


@pytest.mark.unit
def test_pytest_skip_is_error_not_pass(tmp_path):
    # A skipped check exits 0 but proves nothing — must stay un-proven (error),
    # never a false green.
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_skip.py").write_text(
        "import pytest\ndef test_skip():\n    pytest.skip('cannot run here')\n"
    )
    cap = _cap(check_kind="pytest", check_target="checks/test_skip.py::test_skip")
    result, _, _ = run_capability(cap, tmp_path)
    assert result == "error"
