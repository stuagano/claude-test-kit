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
def test_init_then_status_runs_clean(tmp_path):
    init = _caps(["init"], tmp_path)
    assert init.returncode == 0, init.stdout + init.stderr

    for d in ("ctk", "caps", "bin"):
        assert (tmp_path / d).is_dir()
        assert not (tmp_path / d / "__pycache__").exists()
    assert (tmp_path / "capabilities.yaml").is_file()
    assert (tmp_path / "pytest.ini").is_file()
    assert (tmp_path / "conftest.py").is_file()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    st = ctk.run([sys.executable, "-m", "caps", "status"], cwd=str(tmp_path), env=env)
    assert st.returncode == 0, st.stdout + st.stderr  # empty manifest = clean


@pytest.mark.integration
def test_init_is_idempotent_and_repairs_deleted_config(tmp_path):
    assert _caps(["init"], tmp_path).returncode == 0
    (tmp_path / "capabilities.yaml").write_text("capabilities:\n  - keep: me\n")
    (tmp_path / "pytest.ini").unlink()  # simulate a deleted file

    again = _caps(["init"], tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr
    assert (tmp_path / "pytest.ini").is_file()  # repaired
    assert "keep: me" in (tmp_path / "capabilities.yaml").read_text()


@pytest.mark.integration
def test_init_then_add_and_verify_self_contained(tmp_path):
    # First install needs the kit reachable (the project is still empty).
    assert _caps(["init"], tmp_path).returncode == 0

    # A real check that imports the VENDORED ctk — its passing proves the project
    # is self-contained (vendored framework importable via the written pytest.ini).
    (tmp_path / "checks" / "test_demo.py").write_text(
        "import ctk\n"
        "def test_demo():\n"
        "    assert hasattr(ctk, 'run')\n"
    )

    # From here, run with NO PYTHONPATH so ONLY the vendored framework is used —
    # this is the "no PYTHONPATH gymnastics" the drop-in model promises.
    def _vendored(args):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return ctk.run([sys.executable, "-m", "caps", *args], cwd=str(tmp_path), env=env)

    add = _vendored([
        "add", "--id", "demo", "--tier", "cheap",
        "--description", "d", "--given", "g", "--when", "w", "--then", "t",
        "--check", "checks/test_demo.py::test_demo",
    ])
    assert add.returncode == 0, add.stdout + add.stderr

    verify = _vendored(["verify", "--capability", "demo"])
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "demo: pass" in verify.stdout
