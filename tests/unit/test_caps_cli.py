import textwrap
from datetime import datetime, timezone

import pytest
from caps.cli import main


def _project(tmp_path, manifest_body: str):
    (tmp_path / "checks").mkdir(exist_ok=True)
    (tmp_path / "capabilities.yaml").write_text(textwrap.dedent(manifest_body))
    return tmp_path


@pytest.mark.unit
def test_status_on_unproven_capability(tmp_path, capsys):
    _project(tmp_path, """
        capabilities:
          - id: writes-db
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    rc = main(["status"], cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "writes-db" in out
    assert "never proven" in out.lower()


@pytest.mark.unit
def test_status_errors_when_no_manifest(tmp_path, capsys):
    rc = main(["status"], cwd=str(tmp_path))
    err = capsys.readouterr().err
    assert rc == 2
    assert "no capabilities.yaml" in err.lower()
