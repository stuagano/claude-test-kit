import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "caps-stop-gate.sh"


def _run(payload: dict, cwd, python=None):
    env = dict(os.environ)
    env["CAPS_KIT"] = str(REPO_ROOT)
    if python is not None:
        env["CAPS_GATE_PYTHON"] = python
    else:
        env["CAPS_GATE_PYTHON"] = sys.executable
        env["PYTHONPATH"] = str(REPO_ROOT)
    return ctk.run(["bash", str(WRAPPER)], cwd=str(cwd), env=env, input=json.dumps(payload))


@pytest.mark.integration
def test_short_circuits_without_manifest(tmp_path):
    # python points at /bin/false: if the wrapper launched it we'd see a failure
    # or output. No manifest -> must exit 0 BEFORE launching python.
    r = _run({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path, python="/bin/false")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


@pytest.mark.integration
def test_blocks_with_stale_manifest(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(
        textwrap.dedent("""
        capabilities:
          - id: w1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    )
    r = _run({"cwd": str(tmp_path), "stop_hook_active": False}, tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout)["decision"] == "block"


@pytest.mark.integration
def test_resolves_kit_with_unrelated_pythonpath(tmp_path):
    # No CAPS_KIT, and a pre-existing unrelated PYTHONPATH. The wrapper must
    # still find `caps` (kit derived from script location, PYTHONPATH prepended).
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(
        textwrap.dedent("""
        capabilities:
          - id: u1
            description: d
            given: g
            when: w
            then: t
            tier: cheap
            deps: []
            check: checks/test_x.py::test_x
    """)
    )
    env = dict(os.environ)
    env.pop("CAPS_KIT", None)
    env["CAPS_GATE_PYTHON"] = sys.executable  # the venv python (has PyYAML)
    env["PYTHONPATH"] = "/tmp/unrelated-nonexistent-path"
    r = ctk.run(
        ["bash", str(WRAPPER)],
        cwd=str(tmp_path),
        env=env,
        input=json.dumps({"cwd": str(tmp_path), "stop_hook_active": False}),
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "block", (r.stdout, r.stderr)
