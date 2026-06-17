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
