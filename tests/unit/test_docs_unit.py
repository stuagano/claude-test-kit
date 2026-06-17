"""UNIT tests for ctk.docs — the deterministic doc-staleness detectors."""
import pytest

pytestmark = pytest.mark.unit

from ctk.docs import Finding, DocsConfig, find_stale_docs, format_findings


def test_clean_doc_has_no_findings(workspace):
    workspace.write("README.md", "# Title\n\nJust prose, no refs.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert findings == []


def test_finding_str_is_readable():
    f = Finding(doc="a.md", line=3, kind="broken_ref", severity="error",
                message="missing", evidence="x/y.py")
    s = str(f)
    assert "a.md:3" in s and "broken_ref" in s and "x/y.py" in s


def test_broken_ref_flags_missing_code_span_path(workspace):
    workspace.write("README.md", "See `caps/nope.py` for details.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    kinds = [(f.kind, f.severity) for f in findings]
    assert ("broken_ref", "error") in kinds


def test_broken_ref_clean_when_path_exists(workspace):
    workspace.write("caps/real.py", "x = 1\n")
    workspace.write("README.md", "See `caps/real.py`.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "broken_ref"] == []


def test_broken_ref_ignores_illustrative_paths(workspace):
    workspace.write("README.md", "e.g. `path/to/file.py` or `<your-kit>/x.py`.\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "broken_ref"] == []


def test_dead_link_flags_missing_target(workspace):
    workspace.write("README.md", "See [the guide](docs/missing.md).\n")
    findings = find_stale_docs(doc_roots=("README.md",), repo_root=str(workspace.root))
    assert ("dead_link", "error") in [(f.kind, f.severity) for f in findings]


def test_dead_link_clean_for_existing_target(workspace):
    workspace.write("docs/guide.md", "# Guide\n")
    workspace.write("README.md", "See [guide](docs/guide.md).\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "dead_link"] == []


def test_dead_link_warns_on_missing_anchor(workspace):
    workspace.write("docs/guide.md", "# Guide\n\n## Setup\n")
    workspace.write("README.md", "See [setup](docs/guide.md#nonexistent).\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert ("dead_link", "warn") in [(f.kind, f.severity) for f in findings]


def test_dead_link_unreadable_target_is_a_finding(workspace, monkeypatch):
    workspace.write("docs/guide.md", "# Guide\n\n## Setup\n")
    workspace.write("README.md", "See [setup](docs/guide.md#setup).\n")
    import ctk.docs as d
    real_open = open

    def boom(path, *a, **k):
        if str(path).endswith("docs/guide.md"):
            raise OSError("permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", boom)
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert any(f.kind == "dead_link" and "could not read" in f.message
               for f in findings)


def test_orphan_flags_unlinked_doc(workspace):
    workspace.write("README.md", "Start. [guide](docs/guide.md)\n")
    workspace.write("docs/guide.md", "# Guide\n")
    workspace.write("docs/lonely.md", "# Nobody links here\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    orphans = [f.doc for f in findings if f.kind == "orphan"]
    assert "docs/lonely.md" in orphans
    assert "docs/guide.md" not in orphans
    assert "README.md" not in orphans


def test_orphan_exempts_archival_tree(workspace):
    workspace.write("README.md", "# Root\n")
    workspace.write("docs/superpowers/specs/old.md", "# archived\n")
    findings = find_stale_docs(
        doc_roots=("README.md", "docs/"), repo_root=str(workspace.root))
    assert [f for f in findings if f.kind == "orphan"] == []


def test_superseded_via_front_matter(workspace):
    workspace.write("docs/a.md", "---\nsuperseded_by: docs/b.md\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert ("superseded", "warn") in [(f.kind, f.severity) for f in findings]


def test_superseded_via_prose(workspace):
    workspace.write("docs/b.md", "# B\n")
    workspace.write("docs/a.md", "# A\n\nThis is superseded by [B](b.md).\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any(f.kind == "superseded" and f.doc == "docs/a.md" for f in findings)


def test_superseded_via_newer_same_slug(workspace):
    workspace.write("docs/specs/2026-01-01-thing-design.md", "# old\n")
    workspace.write("docs/specs/2026-02-01-thing-design.md", "# new\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    superseded = [f.doc for f in findings if f.kind == "superseded"]
    assert "docs/specs/2026-01-01-thing-design.md" in superseded
    assert "docs/specs/2026-02-01-thing-design.md" not in superseded


def test_assertion_requires_paths_fails_when_missing(workspace):
    workspace.write("docs/a.md", "---\nctk:\n  requires_paths: [caps/gone.py]\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert ("assertion_failed", "error") in [(f.kind, f.severity) for f in findings]


def test_assertion_requires_grep_pass_and_fail(workspace):
    workspace.write("caps/freshness.py", "WINDOW_HOURS = 24\n")
    workspace.write("docs/ok.md",
        "---\nctk:\n  requires_grep:\n    - {file: caps/freshness.py, pattern: '24'}\n---\n# ok\n")
    workspace.write("docs/bad.md",
        "---\nctk:\n  requires_grep:\n    - {file: caps/freshness.py, pattern: '99'}\n---\n# bad\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    bad = [f for f in findings if f.kind == "assertion_failed"]
    assert len(bad) == 1 and bad[0].doc == "docs/bad.md"


def test_malformed_front_matter_is_a_finding_not_a_crash(workspace):
    # ': : :' is invalid YAML inside the front-matter block
    workspace.write("docs/a.md", "---\n: : :\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any(f.severity == "error" and "front matter" in f.message.lower()
               for f in findings)


def test_unreadable_doc_is_a_finding(workspace, monkeypatch):
    workspace.write("docs/a.md", "# A\n")
    import ctk.docs as d
    real_open = open

    def boom(path, *a, **k):
        if str(path).endswith("docs/a.md"):
            raise OSError("permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", boom)
    findings = d.find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any("could not read" in f.message for f in findings)


def test_non_dict_requires_grep_entry_is_a_finding_not_a_crash(workspace):
    # A bare string in requires_grep (not a dict) must not raise AttributeError
    workspace.write("docs/a.md",
        "---\nctk:\n  requires_grep:\n    - bare_string\n---\n# A\n")
    findings = find_stale_docs(doc_roots=("docs/",), repo_root=str(workspace.root))
    assert any(f.kind == "assertion_failed" and "not a mapping" in f.message
               for f in findings)


def test_scan_exempt_suppresses_all_detectors_for_exempt_prefix(workspace):
    # A doc under scan_exempt must produce NO findings even with a broken ref;
    # a non-exempt doc with the same broken ref IS flagged.
    workspace.write("docs/superpowers/x.md", "See `ctk/nope.py` for details.\n")
    workspace.write("docs/live.md", "See `ctk/nope.py` for details.\n")
    config = DocsConfig(scan_exempt=("docs/superpowers/",))
    findings = find_stale_docs(
        doc_roots=("docs/",), repo_root=str(workspace.root), config=config)
    exempt_findings = [f for f in findings if f.doc.startswith("docs/superpowers/")]
    live_findings = [f for f in findings if f.doc == "docs/live.md" and f.kind == "broken_ref"]
    assert exempt_findings == [], f"scan_exempt doc should have no findings, got: {exempt_findings}"
    assert live_findings, "non-exempt doc with broken ref should be flagged"
