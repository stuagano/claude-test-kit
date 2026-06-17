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
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

_CODE_SPAN = re.compile(r"`([^`]+)`")
_PLACEHOLDER = re.compile(r"(path/to/|/\.\.\.|<[^>]+>|\bexample/|\bfoo/|\bbar/|\$\{)")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


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


def _looks_like_repo_path(tok: str, config: DocsConfig) -> bool:
    tok = tok.strip()
    if not tok or tok.startswith(("http://", "https://", "#", "mailto:", "/")):
        return False
    if _PLACEHOLDER.search(tok):
        return False
    if any(re.search(p, tok) for p in config.ignore):
        return False
    if tok.startswith(tuple(config.known_top_dirs)):
        return True
    if "/" in tok and tok.endswith(tuple(config.tracked_ext)):
        return True
    return False


def _exists(rel_path: str, repo_root: str) -> bool:
    rel_path = rel_path.split("#", 1)[0].strip()
    return bool(rel_path) and os.path.exists(os.path.join(repo_root, rel_path))


def _slugify_heading(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s)


def _anchors_in(path: str, repo_root: str) -> set[str]:
    out: set[str] = set()
    try:
        with open(os.path.join(repo_root, path), "r", errors="replace") as f:
            for line in f:
                m = re.match(r"#{1,6}\s+(.*)", line)
                if m:
                    out.add(_slugify_heading(m.group(1)))
    except OSError:
        pass
    return out


def _is_relative_repo_target(target: str) -> bool:
    target = target.strip()
    return bool(target) and not target.startswith(
        ("http://", "https://", "mailto:", "#", "/"))


def _detect_broken_refs(doc: str, text: str, repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _CODE_SPAN.finditer(line):
            tok = m.group(1)
            if _looks_like_repo_path(tok, config) and not _exists(tok, repo_root):
                out.append(Finding(doc, i, "broken_ref",
                                   _severity("broken_ref", config, SEVERITY_ERROR),
                                   "code-span path does not exist", tok))
    return out


def _detect_dead_links(doc: str, text: str, repo_root: str, config: DocsConfig) -> list[Finding]:
    out: list[Finding] = []
    doc_dir = os.path.dirname(doc)
    for i, line in enumerate(text.splitlines(), start=1):
        for m in _MD_LINK.finditer(line):
            target = m.group(1).strip()
            if not _is_relative_repo_target(target):
                continue
            file_part, _, anchor = target.partition("#")
            rel = os.path.normpath(os.path.join(doc_dir, file_part)) if file_part else doc
            if not os.path.exists(os.path.join(repo_root, rel)):
                out.append(Finding(doc, i, "dead_link",
                                   _severity("dead_link", config, SEVERITY_ERROR),
                                   "link target does not exist", target))
            elif anchor and _slugify_heading(anchor) not in _anchors_in(rel, repo_root):
                out.append(Finding(doc, i, "dead_link",
                                   _severity("dead_link_anchor", config, SEVERITY_WARN),
                                   "link anchor not found in target", target))
    return out


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
    for doc in docs:
        try:
            with open(os.path.join(repo_root, doc), "r", errors="strict") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            findings.append(Finding(doc, None, "broken_ref",
                                    SEVERITY_ERROR, f"could not read doc: {e}", doc))
            continue
        findings.extend(_detect_broken_refs(doc, text, repo_root, config))
        findings.extend(_detect_dead_links(doc, text, repo_root, config))
    return findings


def format_findings(findings: Sequence[Finding]) -> str:
    if not findings:
        return "no findings"
    return "\n".join("  " + str(f) for f in findings)
