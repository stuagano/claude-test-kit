import os
import sys
from pathlib import Path

import pytest

import ctk

REPO_ROOT = Path(__file__).resolve().parents[2]


def _caps(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run([sys.executable, "-m", "caps", *args], cwd=str(cwd), env=env)


@pytest.mark.integration
def test_add_then_verify_is_red_then_green(tmp_path):
    add = _caps(
        [
            "add",
            "--id",
            "raw",
            "--tier",
            "cheap",
            "--description",
            "d",
            "--given",
            "g",
            "--when",
            "w",
            "--then",
            "t",
            "--check",
            "checks/test_raw.py::test_raw",
        ],
        tmp_path,
    )
    assert add.returncode == 0, add.stdout + add.stderr

    st = _caps(["status"], tmp_path)
    assert "never proven" in st.stdout.lower()

    # INTEGRITY: scaffolded capability cannot be proven — verify is red.
    v1 = _caps(["verify", "--capability", "raw"], tmp_path)
    assert v1.returncode != 0, v1.stdout + v1.stderr

    # Replace stub with a real passing check -> verify goes green.
    (tmp_path / "checks" / "test_raw.py").write_text("def test_raw():\n    assert True\n")
    v2 = _caps(["verify", "--capability", "raw"], tmp_path)
    assert v2.returncode == 0, v2.stdout + v2.stderr
