import os
import sys
import textwrap
from pathlib import Path

import pytest

import ctk

# Repo root = two levels up from tests/integration/.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_caps(args, cwd):
    env = dict(os.environ)
    # Make `caps` and `ctk` importable in the subprocess running in a temp cwd.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return ctk.run([sys.executable, "-m", "caps", *args], cwd=str(cwd), env=env)


@pytest.mark.integration
def test_broken_capability_blocks_then_fixed_passes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "capabilities.yaml").write_text(
        textwrap.dedent("""
        capabilities:
          - id: word-count-writes-output
            description: the tool writes a parseable count file
            given: an input file
            when: the tool runs
            then: the output file exists and parses
            tier: cheap
            deps: [tool.py]
            check: checks/test_output.py::test_output
    """)
    )
    # A check that asserts the tool produced output.
    (tmp_path / "checks" / "test_output.py").write_text(
        textwrap.dedent("""
        from pathlib import Path
        def test_output():
            assert Path("out.txt").read_text().strip() == "3"
    """)
    )

    # Broken tool: claims success but writes nothing.
    (tmp_path / "tool.py").write_text("print('done')\n")
    r1 = _run_caps(["verify"], tmp_path)
    assert r1.returncode == 1, r1.stdout + r1.stderr
    assert "word-count-writes-output: fail" in r1.stdout

    # Fix the tool so the capability is actually true, and make the check run it.
    (tmp_path / "tool.py").write_text(
        "from pathlib import Path\nPath('out.txt').write_text('3\\n')\n"
    )
    (tmp_path / "checks" / "test_output.py").write_text(
        textwrap.dedent("""
        import subprocess, sys
        from pathlib import Path
        def test_output():
            subprocess.run([sys.executable, "tool.py"], check=True)
            assert Path("out.txt").read_text().strip() == "3"
    """)
    )
    r2 = _run_caps(["verify"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "word-count-writes-output: pass" in r2.stdout

    # Ledger recorded the final pass.
    import json

    ledger = json.loads((tmp_path / ".ctk" / "ledger.json").read_text())
    assert ledger["word-count-writes-output"]["result"] == "pass"
