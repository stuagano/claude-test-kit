from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple, Union

import ctk
from .manifest import Capability

# Reserved shell exit code meaning "could not run / resource unreachable".
ERROR_EXIT = 3

# How much of a failing check's output to keep so the gate can show *why* it
# failed without anyone re-running it. Kept modest so the committed ledger stays
# readable and diff-friendly.
SNIPPET_MAX = 1500


def _snippet(r: "ctk.RunResult") -> str:
    """The most useful tail of a failed check's output. For pytest the failure
    summary lands near the end, so tailing (not heading) is what you want."""
    parts = [p.strip() for p in (r.stdout, r.stderr) if p and p.strip()]
    body = "\n".join(parts).strip()
    if len(body) > SNIPPET_MAX:
        body = "...[truncated]\n" + body[-SNIPPET_MAX:]
    return body


def run_capability(
    capability: Capability, root: Union[str, Path]
) -> Tuple[str, Optional[str], float]:
    """Execute the check and classify the outcome, returning (result, detail,
    duration).

    result is 'pass' | 'fail' | 'error'; detail is a trimmed snippet of the
    check's output on a non-pass outcome (so the gate can show why), or None on
    pass; duration is the wall-clock seconds the check took.

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
            return "pass", None, r.duration
        result = "fail" if r.returncode == 1 else "error"
        return result, _snippet(r), r.duration

    # shell — wrap in /bin/sh so builtins (exit, cd, etc.) work correctly
    r = ctk.run(["/bin/sh", "-c", capability.check_target], cwd=root)
    if r.returncode == 0:
        return "pass", None, r.duration
    result = "error" if r.returncode == ERROR_EXIT else "fail"
    return result, _snippet(r), r.duration
