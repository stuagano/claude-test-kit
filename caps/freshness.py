from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from .ledger import LedgerEntry
from .manifest import Capability


class FreshnessError(Exception):
    """Raised on an unparseable freshness/duration value."""


_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(s: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", s)
    if not m:
        raise FreshnessError(f"bad duration {s!r}; expected forms like '24h', '30m', '2d'")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


def is_fresh(
    capability: Capability,
    entry: Optional[LedgerEntry],
    current_fingerprint: str,
    now: datetime,
) -> bool:
    """True iff the recorded proof is a pass AND still trustworthy.

    code freshness: fingerprint must match the current code.
    duration freshness: the pass must be within the window.
    """
    if entry is None or entry.result != "pass":
        return False
    if capability.freshness == "code":
        return entry.fingerprint == current_fingerprint
    window = parse_duration(capability.freshness)
    return (now - datetime.fromisoformat(entry.at)) < window


def waiver_active(entry: Optional[LedgerEntry], now: datetime) -> bool:
    if entry is None or not entry.waiver:
        return False
    return now < datetime.fromisoformat(entry.waiver["until"])
