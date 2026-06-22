from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass
class LedgerEntry:
    result: str                       # "pass" | "fail" | "error" | "waived"
    at: str                           # ISO-8601 timestamp
    tier: str                         # "cheap" | "live"
    fingerprint: Optional[str] = None
    waiver: Optional[dict] = None     # {"reason": str, "until": isostr}
    detail: Optional[str] = None      # trimmed check output for a fail/error
    files: Optional[dict] = None      # {rel: hash} per-dep proof, for code freshness
    duration: Optional[float] = None  # wall-clock seconds the check last took


def load_ledger(path: Union[str, Path]) -> dict[str, LedgerEntry]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text() or "{}")
    return {k: LedgerEntry(**v) for k, v in raw.items()}


def save_ledger(path: Union[str, Path], entries: dict[str, LedgerEntry]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in entries.items()}
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    # Atomic write (temp + rename in the same dir) so a concurrent verify can't
    # read a half-written ledger. Proven necessary: multiple verifies do overlap.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ledger.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
