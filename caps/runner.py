from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Union

import ctk
from .manifest import Capability

# Reserved shell exit code meaning "could not run / resource unreachable".
ERROR_EXIT = 3

# Checks (esp. live ones that shell out to an LLM) need far more than ctk.run's
# 60s default — a sub-process timeout there gets misclassified as 'error'.
# ponytail: one generous ceiling; make it per-capability only if a check needs more.
CHECK_TIMEOUT = 900.0


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
            timeout=CHECK_TIMEOUT,
        )
        if r.returncode == 0:
            # exit 0 covers both "all passed" and "all skipped". A skip means the
            # check couldn't run (e.g. live CLI unavailable) — that's un-proven,
            # not proven. Treat "nothing actually passed" as error, like ERROR_EXIT.
            return "pass" if re.search(r"\b\d+ passed\b", r.stdout) else "error"
        if r.returncode == 1:
            return "fail"
        return "error"

    # shell — wrap in /bin/sh so builtins (exit, cd, etc.) work correctly
    r = ctk.run(["/bin/sh", "-c", capability.check_target], cwd=root, timeout=CHECK_TIMEOUT)
    if r.returncode == 0:
        return "pass"
    if r.returncode == ERROR_EXIT:
        return "error"
    return "fail"
