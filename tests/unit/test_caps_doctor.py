import json
import textwrap
from datetime import UTC, datetime

import pytest

from caps.doctor import FAIL, OK, WARN, diagnose, exit_code

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


def _project(tmp_path, body):
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent(body))
    return tmp_path


GOOD = """
capabilities:
  - id: c1
    description: d
    given: g
    when: w
    then: t
    tier: cheap
    deps: []
    check: checks/test_x.py::test_x
"""


def _levels(findings):
    return {f.level for f in findings}


@pytest.mark.unit
def test_clean_project_has_no_failures(tmp_path):
    _project(tmp_path, GOOD)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    findings = diagnose(tmp_path, NOW, settings_path=tmp_path / "no-settings.json")
    assert FAIL not in _levels(findings)
    assert exit_code(findings) == 0
    assert any("manifest:" in f.message and f.level == OK for f in findings)


@pytest.mark.unit
def test_missing_check_file_is_a_failure(tmp_path):
    _project(tmp_path, GOOD)  # checks/test_x.py never created
    findings = diagnose(tmp_path, NOW, settings_path=tmp_path / "no-settings.json")
    assert exit_code(findings) == 1
    assert any(
        f.level == FAIL and "check missing" in f.message and "c1" in f.message for f in findings
    )


@pytest.mark.unit
def test_invalid_manifest_is_a_single_failure(tmp_path):
    _project(
        tmp_path,
        """
        capabilities:
          - id: c
            description: d
            given: g
            when: w
            then: t
            tier: bogus
            deps: []
            check: checks/x.py::t
    """,
    )
    findings = diagnose(tmp_path, NOW, settings_path=tmp_path / "no-settings.json")
    assert findings == findings[:1]  # short-circuits to just the manifest failure
    assert findings[0].level == FAIL and "manifest" in findings[0].message
    assert exit_code(findings) == 1


@pytest.mark.unit
def test_hook_detected_when_installed(tmp_path):
    _project(tmp_path, GOOD)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"_caps": "caps-stop-gate", "hooks": [{"type": "command", "command": "x"}]}
                    ]
                }
            }
        )
    )
    findings = diagnose(tmp_path, NOW, settings_path=settings)
    assert any(f.level == OK and "stop-hook: installed" in f.message for f in findings)


@pytest.mark.unit
def test_hook_missing_is_a_warning_not_a_failure(tmp_path):
    _project(tmp_path, GOOD)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": []}}))
    findings = diagnose(tmp_path, NOW, settings_path=settings)
    assert any(f.level == WARN and "stop-hook: not installed" in f.message for f in findings)
    assert exit_code(findings) == 0  # an un-installed hook does not fail doctor


@pytest.mark.unit
@pytest.mark.parametrize("hooks", ["[]", '{"Stop": null}', '{"Stop": 7}', '"nope"'])
def test_malformed_hooks_warns_instead_of_crashing(tmp_path, hooks):
    # doctor diagnoses broken setup; it must not itself crash on odd settings.json.
    _project(tmp_path, GOOD)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    settings = tmp_path / "settings.json"
    settings.write_text(f'{{"hooks": {hooks}}}')
    findings = diagnose(tmp_path, NOW, settings_path=settings)  # must not raise
    assert any(f.level == WARN and "stop-hook" in f.message for f in findings)
    assert exit_code(findings) == 0


@pytest.mark.unit
def test_unproven_capability_reported_in_proof_state(tmp_path):
    _project(tmp_path, GOOD)
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    findings = diagnose(tmp_path, NOW, settings_path=tmp_path / "no-settings.json")
    assert any("proof state" in f.message and "never-proven" in f.message for f in findings)
