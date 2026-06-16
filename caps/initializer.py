from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Union

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
FRAMEWORK_DIRS = ("ctk", "caps", "bin")
_CONFTEST_WARNING = (
    "kept your existing conftest.py; until you add the kit's `workspace` fixture "
    "and the autouse `fail_on_error_log` guard to it, the error-log guard is OFF "
    "and any vendored check using the `workspace` fixture will error. See the "
    "kit's conftest.py for the two fixtures to copy in."
)


@dataclass
class StepResult:
    """One thing `init` did (or chose not to do), for the CLI to print.

    action is one of: created | skipped | overwritten | warned | installed
                      | instructed
    """
    action: str
    target: str
    detail: str = ""


def kit_root() -> Path:
    """The kit being vendored: the dir containing the live caps/ package."""
    return Path(__file__).resolve().parent.parent


def _vendor_one(src: Path, dst: Path, force: bool) -> StepResult:
    name = dst.name
    if dst.exists():
        if not force:
            return StepResult("skipped", str(dst), f"{name}/ already present")
        shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=_IGNORE)
        return StepResult("overwritten", str(dst), f"re-vendored {name}/ (--force)")
    shutil.copytree(src, dst, ignore=_IGNORE)
    return StepResult("created", str(dst), f"vendored {name}/")


def ensure_conftest(target: Union[str, Path], kit: Union[str, Path]) -> StepResult:
    target, kit = Path(target), Path(kit)
    dst = target / "conftest.py"
    if dst.exists():
        return StepResult("warned", str(dst), _CONFTEST_WARNING)
    shutil.copy2(kit / "conftest.py", dst)
    return StepResult("created", str(dst), "copied conftest.py (workspace + error-log guard)")


_PYTEST_INI = """\
[pytest]
# Written by `caps init`. Lets vendored `ctk`/`caps` import without installing,
# and registers the markers their checks use.
pythonpath = .
addopts = -ra --strict-markers
markers =
    unit: fast, isolated tests with no real I/O (mock the boundaries)
    integration: tests that hit real dependencies (DB, HTTP, subprocess)
    slow: long-running tests, excluded from the quick loop
    allow_error_logs: permit ERROR/CRITICAL logs without failing the test
"""


def _has_pytest_config(target: Path) -> bool:
    if (target / "pytest.ini").is_file():
        return True
    pp = target / "pyproject.toml"
    if pp.is_file() and "[tool.pytest.ini_options]" in pp.read_text():
        return True
    sc = target / "setup.cfg"
    if sc.is_file() and "[tool:pytest]" in sc.read_text():
        return True
    tox = target / "tox.ini"
    if tox.is_file() and "[pytest]" in tox.read_text():
        return True
    return False


def ensure_pytest_config(target: Union[str, Path]) -> StepResult:
    target = Path(target)
    if _has_pytest_config(target):
        return StepResult(
            "skipped", str(target),
            "existing pytest config found; ensure it sets `pythonpath = .` and the "
            "unit/integration/slow/allow_error_logs markers (see the kit's pytest.ini)",
        )
    dst = target / "pytest.ini"
    dst.write_text(_PYTEST_INI)
    return StepResult("created", str(dst), "wrote minimal pytest.ini")


def vendor_framework(
    target: Union[str, Path], kit: Union[str, Path], force: bool
) -> list[StepResult]:
    target, kit = Path(target), Path(kit)
    if target.resolve() == kit.resolve():
        raise ValueError("init target is the kit itself; refusing to vendor onto the source")
    results: list[StepResult] = []
    for name in FRAMEWORK_DIRS:
        src = kit / name
        if not src.is_dir():
            continue  # nothing to vendor for a kit missing this dir
        results.append(_vendor_one(src, target / name, force))
    return results
