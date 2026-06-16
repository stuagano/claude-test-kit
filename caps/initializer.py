from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Union

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
FRAMEWORK_DIRS = ("ctk", "caps", "bin")


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
