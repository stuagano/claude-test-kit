from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .manifest import load_manifest
from .ledger import load_ledger
from .fingerprint import fingerprint
from .freshness import is_fresh, waiver_active

MANIFEST_NAME = "capabilities.yaml"
LEDGER_REL = Path(".ctk") / "ledger.json"


def find_root(start: Path) -> Optional[Path]:
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / MANIFEST_NAME).is_file():
            return d
    return None


def _status_label(cap, entry, root, now) -> str:
    if waiver_active(entry, now):
        return "waived"
    if entry is None:
        return "never proven"
    if entry.result in ("fail", "error"):
        return entry.result
    fp = fingerprint(cap, root) if cap.freshness == "code" else ""
    return "proven" if is_fresh(cap, entry, fp, now) else "stale"


def cmd_status(root: Path, now: datetime) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    ledger = load_ledger(root / LEDGER_REL)
    glyph = {"proven": "OK ", "stale": "STALE", "fail": "FAIL",
             "error": "ERR ", "waived": "WAIV", "never proven": "----"}
    for cap in caps:
        label = _status_label(cap, ledger.get(cap.id), root, now)
        print(f"[{glyph.get(label, '?'):5}] {cap.id:30} {label}")
    return 0


def main(argv=None, cwd: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="caps")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show capability status (read-only)")

    args = parser.parse_args(argv)
    start = Path(cwd) if cwd else Path.cwd()
    root = find_root(start)
    if root is None:
        print(f"error: no {MANIFEST_NAME} found from {start}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    if args.command == "status":
        return cmd_status(root, now)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
