from __future__ import annotations

from pathlib import Path
from typing import Optional

MANIFEST_NAME = "capabilities.yaml"
LEDGER_REL = Path(".ctk") / "ledger.json"


def find_root(start: Path) -> Optional[Path]:
    start = Path(start).resolve()
    for d in (start, *start.parents):
        if (d / MANIFEST_NAME).is_file():
            return d
    return None
