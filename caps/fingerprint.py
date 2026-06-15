from __future__ import annotations

import glob
import hashlib
from pathlib import Path
from typing import Union

from .manifest import Capability


def _collect_files(capability: Capability, root: Path) -> list[Path]:
    files: list[Path] = []
    # The check file itself (pytest node "path::test" -> "path"). Shell checks
    # have no single source file, so only their deps are hashed.
    if capability.check_kind == "pytest":
        files.append(root / capability.check_target.split("::", 1)[0])
    for pattern in capability.deps:
        for match in glob.glob(str(root / pattern), recursive=True):
            files.append(Path(match))
    return files


def fingerprint(capability: Capability, root: Union[str, Path]) -> str:
    """Hash the check file plus every file matched by deps globs.

    Deterministic: files are sorted by their path relative to root. A missing
    file hashes as a literal "<missing>" marker so deletion changes the result.
    """
    root = Path(root)
    h = hashlib.sha256()
    seen: set[str] = set()
    for f in _collect_files(capability, root):
        f = f if f.is_absolute() else (root / f)
        try:
            rel = str(f.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(f)
        seen.add(rel)
    for rel in sorted(seen):
        p = root / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.is_file() else b"<missing>")
    return "sha256:" + h.hexdigest()
