from __future__ import annotations

import sys
from pathlib import Path
from typing import Union

import ctk
from .manifest import Capability

# Reserved shell exit code meaning "could not run / resource unreachable".
ERROR_EXIT = 3


def run_capability(capability: Capability, root: Union[str, Path]) -> str:
    """Execute the check and classify the outcome: 'pass' | 'fail' | 'error'.

    pytest: exit 0 -> pass, 1 -> fail, anything else (collection/internal error,
            no tests) -> error.
    shell:  exit 0 -> pass, ERROR_EXIT (3) -> error, any other non-zero -> fail.
    """
    root = str(root)
    if capability.check_kind == "pytest":
        r = ctk.run(
            [sys.executable, "-m", "pytest", capability.check_target, "-q", "-p", "no:cacheprovider"],
            cwd=root,
        )
        if r.returncode == 0:
            return "pass"
        if r.returncode == 1:
            return "fail"
        return "error"

    # shell — wrap in /bin/sh so builtins (exit, cd, etc.) work correctly
    r = ctk.run(["/bin/sh", "-c", capability.check_target], cwd=root)
    if r.returncode == 0:
        return "pass"
    if r.returncode == ERROR_EXIT:
        return "error"
    return "fail"
