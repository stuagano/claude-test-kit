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
