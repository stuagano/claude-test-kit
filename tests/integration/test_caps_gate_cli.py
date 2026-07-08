import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]


def _gate(payload: dict, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run(
        [sys.executable, "-m", "caps", "gate"], cwd=str(cwd), env=env, input=json.dumps(payload)
    )


@pytest.mark.integration
def test_gate_subprocess_blocks_then_clean(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(
        textwrap.dedent("""
        capabilities:
          - id: e1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_ok.py::test_ok
    """)
    )
    (tmp_path / "checks" / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    r1 = _gate({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r1.returncode == 0
    assert json.loads(r1.stdout)["decision"] == "block"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    ctk.run([sys.executable, "-m", "caps", "verify"], cwd=str(tmp_path), env=env).ok()
    r2 = _gate({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r2.returncode == 0
    assert r2.stdout.strip() == ""
