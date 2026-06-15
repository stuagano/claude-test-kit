from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .project import MANIFEST_NAME, LEDGER_REL, find_root
from .manifest import load_manifest
from .ledger import load_ledger
from .state import capability_state, BLOCK_STATES


@dataclass
class GateDecision:
    block: bool
    reason: Optional[str] = None   # shown to Claude when block is True
    note: Optional[str] = None     # non-blocking additionalContext


def resolve_root(payload: dict) -> Optional[Path]:
    cwd = payload.get("cwd")
    if cwd:
        r = find_root(Path(cwd))
        if r:
            return r
    tp = payload.get("transcript_path")
    if tp:
        r = find_root(Path(tp).parent)
        if r:
            return r
    return None


def decide(payload: dict, now: datetime) -> GateDecision:
    if payload.get("stop_hook_active"):
        return GateDecision(block=False)

    root = resolve_root(payload)
    if root is None:
        return GateDecision(block=False)

    caps = load_manifest(root / MANIFEST_NAME)
    ledger = load_ledger(root / LEDGER_REL)

    blocking: list[tuple] = []   # (cap, state)
    expired: list = []           # cap
    for cap in caps:
        state = capability_state(cap, ledger.get(cap.id), root, now)
        if state in BLOCK_STATES:
            blocking.append((cap, state))
        elif state == "time-expired":
            expired.append(cap)

    note = None
    if expired:
        ids = ", ".join(c.id for c in expired)
        note = f"live capability time-expired (re-verify when convenient): {ids}"

    if not blocking:
        return GateDecision(block=False, note=note)

    lines = ["✗ Capabilities not proven & fresh — resolve before finishing:"]
    for cap, state in blocking:
        lines.append(f"  • {cap.id} [{state}]: {cap.then}")
        lines.append(f"    → python -m caps verify --capability {cap.id}")
    if note:
        lines.append(f"  (note) {note}")
    lines.append("Full status: python -m caps status   ·   "
                 "can't prove now? python -m caps ack <id> --reason \"...\"")
    return GateDecision(block=True, reason="\n".join(lines), note=note)
