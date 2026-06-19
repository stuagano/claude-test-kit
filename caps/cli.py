from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .manifest import load_manifest, ManifestError
from .ledger import load_ledger, LedgerEntry, save_ledger
from .fingerprint import fingerprint, file_fingerprints, changed_deps, FILE_MAP_LIMIT
from .freshness import is_fresh, waiver_active, parse_duration, FreshnessError
from .runner import run_capability
from .state import capability_state, BLOCK_STATES
from .project import MANIFEST_NAME, LEDGER_REL, find_root
from .gate import decide
from .hookinstall import install_hook, uninstall_hook
from .manifest_edit import add_capability, ManifestEditError
from .initializer import init_project, kit_root
from .doctor import diagnose, exit_code, Finding, OK, WARN, FAIL


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


# A check counts as "slowed down" only when it both at least doubled AND grew by
# a meaningful absolute amount — so sub-second jitter never cries wolf.
SLOW_REGRESSION_FACTOR = 2.0
SLOW_REGRESSION_FLOOR = 0.5  # seconds


def _slowdown_note(cap_id: str, prev: Optional[float], new: float) -> Optional[str]:
    """Flag a real timing regression against the previously-recorded duration."""
    if prev and new >= prev * SLOW_REGRESSION_FACTOR and (new - prev) >= SLOW_REGRESSION_FLOOR:
        return f"{cap_id}: slower — {prev:.2f}s -> {new:.2f}s (check timing regressed)"
    return None


def _fmt_duration(seconds: Optional[float]) -> str:
    return "" if seconds is None else f" ({seconds:.2f}s)"


def _print_warnings(caps) -> None:
    for cap in caps:
        for w in cap.warnings:
            print(f"warning: {cap.id}: {w}", file=sys.stderr)


def _capability_report(cap, entry, state, root) -> dict:
    """One capability's machine-readable status: always id/state/tier/then, plus
    the evidence relevant to that state (detail, changed deps, waiver, at)."""
    rep = {"id": cap.id, "state": state, "tier": cap.tier, "then": cap.then}
    if entry is not None:
        rep["at"] = entry.at
        if entry.duration is not None:
            rep["duration"] = entry.duration
    if cap.warnings:
        rep["warnings"] = list(cap.warnings)
    if state in ("fail", "error") and entry is not None and entry.detail:
        rep["detail"] = entry.detail
    if state == "code-stale":
        ch = changed_deps(cap, getattr(entry, "files", None), root)
        if ch:
            rep["changed"] = ch
    if state == "waived" and entry is not None and entry.waiver:
        rep["waiver"] = entry.waiver
    return rep


def cmd_status(root: Path, now: datetime, as_json: bool = False) -> int:
    caps = load_manifest(root / MANIFEST_NAME)
    ledger = load_ledger(root / LEDGER_REL)
    reports = []
    for cap in caps:
        entry = ledger.get(cap.id)
        state = capability_state(cap, entry, root, now)
        reports.append((cap, entry, state, _capability_report(cap, entry, state, root)))

    if as_json:
        summary: dict = {}
        for _, _, state, _ in reports:
            summary[state] = summary.get(state, 0) + 1
        blocking = [r["id"] for *_, r in reports if r["state"] in BLOCK_STATES]
        print(json.dumps({
            "root": str(root),
            "capabilities": [r for *_, r in reports],
            "summary": summary,
            "blocking": blocking,
            "ok": not blocking,
        }, indent=2))
        return 0

    _print_warnings(caps)
    for cap, entry, state, rep in reports:
        label = _DISPLAY[state]
        line = f"[{_GLYPH.get(label, '?'):5}] {cap.id:30} {label:12}{_fmt_duration(rep.get('duration'))}"
        if "changed" in rep:
            changed = rep["changed"]
            more = f", +{len(changed) - 3}" if len(changed) > 3 else ""
            line += f"  (changed: {', '.join(changed[:3])}{more})"
        print(line.rstrip())
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
        prev = ledger.get(cap.id)
        result, detail, duration = run_capability(cap, root)
        fmap = None
        if cap.freshness == "code":
            fmap = file_fingerprints(cap, root)
            if len(fmap) > FILE_MAP_LIMIT:
                fmap = None   # broad glob: keep the ledger lean, skip itemizing
        ledger[cap.id] = LedgerEntry(
            result=result,
            at=now.isoformat(),
            tier=cap.tier,
            fingerprint=fingerprint(cap, root) if cap.freshness == "code" else None,
            waiver=None,
            detail=detail if result != "pass" else None,
            files=fmap,
            duration=round(duration, 3),
        )
        print(f"{cap.id}: {result}{_fmt_duration(duration)}")
        slow = _slowdown_note(cap.id, prev.duration if prev else None, duration)
        if slow:
            print(slow, file=sys.stderr)
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


_DOCTOR_GLYPH = {OK: " OK ", WARN: "WARN", FAIL: "FAIL"}


def cmd_doctor(root: Path, now: datetime, settings_path, as_json: bool = False) -> int:
    findings = diagnose(root, now, settings_path)
    if as_json:
        print(json.dumps({
            "root": str(root),
            "findings": [{"level": f.level, "message": f.message} for f in findings],
            "ok": exit_code(findings) == 0,
        }, indent=2))
        return exit_code(findings)
    print(f"caps doctor — {root}")
    for f in findings:
        print(f"[{_DOCTOR_GLYPH[f.level]}] {f.message}")
    n_fail = sum(f.level == FAIL for f in findings)
    n_warn = sum(f.level == WARN for f in findings)
    print(f"{n_fail} error(s), {n_warn} warning(s)")
    return exit_code(findings)


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
    st = sub.add_parser("status", help="show capability status (read-only)")
    st.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON (state, detail, changed deps, blocking set)")
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
    doc = sub.add_parser("doctor", help="diagnose project setup (manifest, checks, ledger, hook)")
    doc.add_argument("--settings", default=None,
                     help="settings.json to check for the Stop hook (default: ~/.claude/settings.json)")
    doc.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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
            return cmd_status(root, now, args.json)
        if args.command == "verify":
            return cmd_verify(root, now, args.only, args.stale)
        if args.command == "ack":
            return cmd_ack(root, now, args.capability, args.reason, args.for_)
        if args.command == "doctor":
            return cmd_doctor(root, now, args.settings, args.json)
    except (ManifestError, FreshnessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
