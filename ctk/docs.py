"""
Deterministic doc-staleness detection.

Mirrors ctk.lint.find_swallowed_exceptions: scan a set of docs and return a
list of Finding objects (empty == clean). Pure function of repo contents — no
wall-clock, no network — so it can back a `cheap` caps capability whose proof
is honest under fingerprint freshness.

Detectors: broken_ref, dead_link, orphan, superseded, assertion_failed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"


@dataclass
class Finding:
    doc: str
    line: Optional[int]
    kind: str        # broken_ref | dead_link | orphan | superseded | assertion_failed
    severity: str    # error | warn
    message: str
    evidence: str = ""

    def __str__(self) -> str:
        loc = f"{self.doc}:{self.line}" if self.line else self.doc
        ev = f"  ({self.evidence})" if self.evidence else ""
        return f"{loc}  [{self.kind}/{self.severity}]  {self.message}{ev}"


@dataclass
class DocsConfig:
    doc_roots: Sequence[str] = ("docs/", "README.md", "SKILL.md", "CLAUDE.md")
    entrypoints: Sequence[str] = ("README.md", "SKILL.md", "CLAUDE.md")
    ignore: Sequence[str] = ()                       # regexes: path-like tokens to skip
    orphan_exempt: Sequence[str] = ("docs/superpowers/",)
    known_top_dirs: Sequence[str] = ("caps/", "ctk/", "bin/", "tests/", "docs/", "examples/")
    tracked_ext: Sequence[str] = (
        ".py", ".md", ".sh", ".yaml", ".yml", ".txt", ".ini", ".toml", ".json",
    )
    severity_overrides: dict = field(default_factory=dict)   # kind -> severity
    direction: dict = field(default_factory=dict)            # consumed by docs_direction

    @classmethod
    def from_yaml(cls, path: str) -> "DocsConfig":
        import yaml
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        known = {f_.name for f_ in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _severity(kind: str, config: DocsConfig, default: str) -> str:
    return config.severity_overrides.get(kind, default)


def _iter_docs(doc_roots: Sequence[str], repo_root: str) -> list[str]:
    """Return repo-relative paths of all .md docs under the given roots."""
    out: list[str] = []
    for root in doc_roots:
        abs_root = os.path.join(repo_root, root)
        if os.path.isfile(abs_root):
            if abs_root.endswith(".md"):
                out.append(os.path.relpath(abs_root, repo_root))
        elif os.path.isdir(abs_root):
            for dirpath, dirs, files in os.walk(abs_root):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
                for fn in files:
                    if fn.endswith(".md"):
                        p = os.path.join(dirpath, fn)
                        out.append(os.path.relpath(p, repo_root))
    return sorted(set(out))


def find_stale_docs(
    doc_roots: Sequence[str] = ("docs/", "README.md", "SKILL.md", "CLAUDE.md"),
    repo_root: str = ".",
    config: Optional[DocsConfig] = None,
) -> list[Finding]:
    config = config or DocsConfig(doc_roots=doc_roots)
    findings: list[Finding] = []
    docs = _iter_docs(doc_roots, repo_root)
    # detectors are added in later tasks
    return findings


def format_findings(findings: Sequence[Finding]) -> str:
    if not findings:
        return "no findings"
    return "\n".join("  " + str(f) for f in findings)
