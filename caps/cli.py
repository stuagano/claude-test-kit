from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .manifest import load_manifest, ManifestError
from .ledger import load_ledger, LedgerEntry, save_ledger
from .fingerprint import fingerprint
from .freshness import is_fresh, waiver_active, parse_duration, FreshnessError
from .runner import run_capability
from .state import capability_state, BLOCK_STATES
from .project import MANIFEST_NAME, LEDGER_REL, find_root
from .gate import decide
from .hookinstall import install_hook, uninstall_hook
from .manifest_edit import add_capability, ManifestEditError
from .initializer import init_project, kit_root


_DISPLAY = {
    "proven": "proven",
    "never-proven": "never proven",
    "fail": "fail",
    "error": "error",
    "code-stale": "stale",
    "time-expired": "expired",
    "waived": "waived",
}
_GLYPH = {
    "proven": "OK ", "never proven": "----", "fail": "FAIL",
    "error": "ERR ", "stale": "STALE", "expired": "EXP ", "waived": "WAIV",
}


def _print_warnings(caps) -> None:
    for cap in caps:
        for w in cap.warnings:
            print(f"warning: {cap.id}: {w}", file=sys.stderr)


def cmd_status(root: Path, now: datetime) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    _print_warnings(caps)
    ledger = load_ledger(root / LEDGER_REL)
    for cap in caps:
        state = capability_state(cap, ledger.get(cap.id), root, now)
        label = _DISPLAY[state]
        print(f"[{_GLYPH.get(label, '?'):5}] {cap.id:30} {label}")
    return 0


def cmd_verify(root: Path, now: datetime, only: Optional[str],
               stale: bool = False) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    _print_warnings(caps)
    ledger = load_ledger(root / LEDGER_REL)
    if only is not None:
        caps = [c for c in caps if c.id == only]
        if not caps:
            print(f"error: no capability with id {only!r}", file=sys.stderr)
            return 2
    elif stale:
        # Re-prove exactly the set the Stop-hook gate would block on, in one go.
        caps = [c for c in caps
                if capability_state(c, ledger.get(c.id), root, now) in BLOCK_STATES]
        if not caps:
            print("nothing stale — all capabilities are proven & fresh (or waived)")
            return 0

    worst_ok = True
    for cap in caps:
        # An active waiver suppresses the check during a bare verify; the
        # existing waived entry is preserved. An explicit --capability overrides.
        if only is None and waiver_active(ledger.get(cap.id), now):
            print(f"{cap.id}: skipped (waived)")
            continue
        result, detail = run_capability(cap, root)
        ledger[cap.id] = LedgerEntry(
            result=result,
            at=now.isoformat(),
            tier=cap.tier,
            fingerprint=fingerprint(cap, root) if cap.freshness == "code" else None,
            waiver=None,
            detail=detail if result != "pass" else None,
        )
        print(f"{cap.id}: {result}")
        if result != "pass":
            worst_ok = False
            if detail:
                print(detail, file=sys.stderr)
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


def cmd_gate(stdin_text: str, now: datetime) -> int:
    try:
        payload = json.loads(stdin_text or "{}")
        decision = decide(payload, now)
    except Exception as e:  # fail open, but visibly
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": f"caps gate failed: {e} — capability enforcement skipped this turn",
        }}))
        return 0
    if decision.block:
        print(json.dumps({"decision": "block", "reason": decision.reason}))
    elif decision.note:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": decision.note}}))
    return 0


def cmd_init(target: str, force: bool, install_deps: bool) -> int:
    try:
        results = init_project(target, kit=kit_root(), force=force, install_deps=install_deps)
    except ValueError as e:
        # e.g. `init --force` aimed at the kit itself — refuse cleanly, not with a traceback.
        print(f"error: {e}", file=sys.stderr)
        return 2
    for r in results:
        print(f"  {r.action:11} {r.detail}")
    print()
    print("Next steps:")
    print("  1. Add a capability:  python -m caps add --id <id> --tier <cheap|live> ...")
    print("  2. Prove it:          python -m caps verify")
    print("  3. (optional) enforce on every turn — the wrapper is vendored at")
    print("     bin/caps-stop-gate.sh, but the hook is NOT installed by init.")
    print("     Once this project has a Python with PyYAML, register it with:")
    print("       CAPS_GATE_PYTHON=/path/to/python python -m caps install-hook")
    return 0


def main(argv=None, cwd: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="caps")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show capability status (read-only)")
    v = sub.add_parser("verify", help="run checks and record proof")
    vsel = v.add_mutually_exclusive_group()
    vsel.add_argument("--capability", dest="only", default=None,
                      help="verify a single capability by id")
    vsel.add_argument("--stale", action="store_true",
                      help="re-prove only the capabilities the gate would block on")
    a = sub.add_parser("ack", help="record a time-boxed waiver for a capability")
    a.add_argument("capability", help="capability id to waive")
    a.add_argument("--reason", required=True, help="why it can't be proven now")
    a.add_argument("--for", dest="for_", default="24h",
                   help="waiver duration, e.g. 24h (default), 2d, 30m")
    sub.add_parser("gate", help="Stop-hook gate: read hook JSON on stdin, emit allow/block")
    ih = sub.add_parser("install-hook", help="register the Stop-hook gate in settings.json")
    ih.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ih.add_argument("--command", dest="hook_command", default=None,
                    help="hook command (defaults to this kit's bin/caps-stop-gate.sh)")
    uh = sub.add_parser("uninstall-hook", help="remove the Stop-hook gate from settings.json")
    uh.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))

    ad = sub.add_parser("add", help="add a capability to the manifest (never-proven)")
    ad.add_argument("--id", required=True)
    ad.add_argument("--description", required=True)
    ad.add_argument("--given", required=True)
    ad.add_argument("--when", required=True)
    ad.add_argument("--then", required=True)
    ad.add_argument("--tier", required=True, choices=["cheap", "live"])
    ad.add_argument("--deps", action="append", default=[],
                    help="dep glob (repeat for multiple)")
    grp = ad.add_mutually_exclusive_group(required=True)
    grp.add_argument("--check", help="pytest node, e.g. checks/test_x.py::test_x")
    grp.add_argument("--shell", help="shell command; exit 0 = proven")
    ad.add_argument("--manifest", default=None, help="path to capabilities.yaml")

    ini = sub.add_parser("init", help="vendor the framework into a project (drop-in installer)")
    ini.add_argument("--target", default=None, help="target dir (default: cwd)")
    ini.add_argument("--force", action="store_true",
                     help="re-overwrite vendored ctk/caps/bin (never user files)")
    ini.add_argument("--install-deps", dest="install_deps", action="store_true",
                     help="pip-install PyYAML into the active environment")

    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)

    if args.command == "gate":
        return cmd_gate(sys.stdin.read(), now)
    if args.command == "install-hook":
        kit = Path(__file__).resolve().parent.parent
        cmd = args.hook_command or str(kit / "bin" / "caps-stop-gate.sh")
        venv_py = kit / ".venv" / "bin" / "python"
        if not venv_py.exists():
            print(f"warning: {venv_py} not found — run ./run_tests.sh once so the "
                  f"hook has an interpreter (gate will fail open until then)", file=sys.stderr)
        install_hook(args.settings, command=cmd)
        print(f"installed Stop-hook gate -> {args.settings}")
        return 0
    if args.command == "uninstall-hook":
        uninstall_hook(args.settings)
        print(f"removed Stop-hook gate from {args.settings}")
        return 0

    if args.command == "add":
        if args.manifest:
            manifest_path = Path(args.manifest)
        else:
            start = Path(cwd) if cwd else Path.cwd()
            manifest_path = (find_root(start) or start) / MANIFEST_NAME
        try:
            add_capability(
                manifest_path, id=args.id, description=args.description,
                given=args.given, when=args.when, then=args.then,
                tier=args.tier, deps=args.deps, check=args.check, shell=args.shell,
            )
        except (ManifestEditError, ManifestError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"added capability {args.id!r} (never-proven) -> {manifest_path}")
        return 0

    if args.command == "init":
        target = args.target or (cwd if cwd else str(Path.cwd()))
        return cmd_init(target, args.force, args.install_deps)

    start = Path(cwd) if cwd else Path.cwd()
    root = find_root(start)
    if root is None:
        print(f"error: no {MANIFEST_NAME} found from {start}", file=sys.stderr)
        return 2

    try:
        if args.command == "status":
            return cmd_status(root, now)
        if args.command == "verify":
            return cmd_verify(root, now, args.only, args.stale)
        if args.command == "ack":
            return cmd_ack(root, now, args.capability, args.reason, args.for_)
    except (ManifestError, FreshnessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
