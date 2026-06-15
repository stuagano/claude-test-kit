from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .manifest import load_manifest, ManifestError
from .ledger import load_ledger, LedgerEntry, save_ledger
from .fingerprint import fingerprint
from .freshness import is_fresh, waiver_active, parse_duration, FreshnessError
from .runner import run_capability

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


def _print_warnings(caps) -> None:
    for cap in caps:
        for w in cap.warnings:
            print(f"warning: {cap.id}: {w}", file=sys.stderr)


def cmd_status(root: Path, now: datetime) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    _print_warnings(caps)
    ledger = load_ledger(root / LEDGER_REL)
    glyph = {"proven": "OK ", "stale": "STALE", "fail": "FAIL",
             "error": "ERR ", "waived": "WAIV", "never proven": "----"}
    for cap in caps:
        label = _status_label(cap, ledger.get(cap.id), root, now)
        print(f"[{glyph.get(label, '?'):5}] {cap.id:30} {label}")
    return 0


def cmd_verify(root: Path, now: datetime, only: Optional[str]) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    _print_warnings(caps)
    if only is not None:
        caps = [c for c in caps if c.id == only]
        if not caps:
            print(f"error: no capability with id {only!r}", file=sys.stderr)
            return 2

    ledger = load_ledger(root / LEDGER_REL)
    worst_ok = True
    for cap in caps:
        # An active waiver suppresses the check during a bare verify; the
        # existing waived entry is preserved. An explicit --capability overrides.
        if only is None and waiver_active(ledger.get(cap.id), now):
            print(f"{cap.id}: skipped (waived)")
            continue
        result = run_capability(cap, root)
        ledger[cap.id] = LedgerEntry(
            result=result,
            at=now.isoformat(),
            tier=cap.tier,
            fingerprint=fingerprint(cap, root) if cap.freshness == "code" else None,
            waiver=None,
        )
        print(f"{cap.id}: {result}")
        if result != "pass":
            worst_ok = False
    save_ledger(root / LEDGER_REL, ledger)
    return 0 if worst_ok else 1


def cmd_ack(root: Path, now: datetime, cap_id: str, reason: str, for_: str) -> int:
    caps = {c.id: c for c in load_manifest(root / MANIFEST_NAME)}
    if cap_id not in caps:
        print(f"error: no capability with id {cap_id!r}", file=sys.stderr)
        return 2
    until = (now + parse_duration(for_)).isoformat()
    ledger = load_ledger(root / LEDGER_REL)
    ledger[cap_id] = LedgerEntry(
        result="waived",
        at=now.isoformat(),
        tier=caps[cap_id].tier,
        fingerprint=None,
        waiver={"reason": reason, "until": until},
    )
    save_ledger(root / LEDGER_REL, ledger)
    print(f"{cap_id}: waived until {until} ({reason})")
    return 0


def main(argv=None, cwd: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="caps")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show capability status (read-only)")
    v = sub.add_parser("verify", help="run checks and record proof")
    v.add_argument("--capability", dest="only", default=None,
                   help="verify a single capability by id")
    a = sub.add_parser("ack", help="record a time-boxed waiver for a capability")
    a.add_argument("capability", help="capability id to waive")
    a.add_argument("--reason", required=True, help="why it can't be proven now")
    a.add_argument("--for", dest="for_", default="24h",
                   help="waiver duration, e.g. 24h (default), 2d, 30m")

    args = parser.parse_args(argv)
    start = Path(cwd) if cwd else Path.cwd()
    root = find_root(start)
    if root is None:
        print(f"error: no {MANIFEST_NAME} found from {start}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    try:
        if args.command == "status":
            return cmd_status(root, now)
        if args.command == "verify":
            return cmd_verify(root, now, args.only)
        if args.command == "ack":
            return cmd_ack(root, now, args.capability, args.reason, args.for_)
    except (ManifestError, FreshnessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
