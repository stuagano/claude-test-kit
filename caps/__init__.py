"""
caps — the capability-verification layer for claude-test-kit.

Declares the capabilities a project promises (capabilities.yaml), proves them
against reality, and records proof in a committed ledger. Built on ctk.

Phase 1 (this package) is the manual runner: `python -m caps verify`.
"""

from .manifest import Capability, load_manifest, ManifestError

try:
    from .ledger import LedgerEntry, load_ledger, save_ledger
except ImportError:
    pass

try:
    from .fingerprint import fingerprint
except ImportError:
    pass

try:
    from .freshness import parse_duration, is_fresh, waiver_active, FreshnessError
except ImportError:
    pass

try:
    from .runner import run_capability
except ImportError:
    pass

__all__ = [
    "Capability",
    "load_manifest",
    "ManifestError",
    "LedgerEntry",
    "load_ledger",
    "save_ledger",
    "fingerprint",
    "parse_duration",
    "is_fresh",
    "waiver_active",
    "FreshnessError",
    "run_capability",
]

__version__ = "0.1.0"
