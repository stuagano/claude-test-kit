import pytest
from caps.manifest import Capability
from caps.fingerprint import fingerprint


def _cap(**kw):
    base = dict(
        id="c", description="d", given="g", when="w", then="t",
        tier="cheap", deps=[], freshness="code",
        check_kind="pytest", check_target="checks/test_x.py::test_x",
    )
    base.update(kw)
    return Capability(**base)


@pytest.mark.unit
def test_fingerprint_changes_when_dep_changes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "ingest.py").write_text("x = 1\n")
    cap = _cap(deps=["ingest.py"])

    fp1 = fingerprint(cap, tmp_path)
    (tmp_path / "ingest.py").write_text("x = 2\n")
    fp2 = fingerprint(cap, tmp_path)

    assert fp1 != fp2
    assert fp1.startswith("sha256:")


@pytest.mark.unit
def test_fingerprint_stable_when_nothing_changes(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    cap = _cap()
    assert fingerprint(cap, tmp_path) == fingerprint(cap, tmp_path)


@pytest.mark.unit
def test_glob_deps_matched(tmp_path):
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.py").write_text("a = 1\n")
    cap = _cap(deps=["lib/**"])

    fp1 = fingerprint(cap, tmp_path)
    (tmp_path / "lib" / "a.py").write_text("a = 99\n")
    assert fingerprint(cap, tmp_path) != fp1


@pytest.mark.unit
def test_ignores_pycache_and_pyc(tmp_path):
    # Build artifacts under a dep glob must not affect the fingerprint, or a
    # capability goes "stale" every time Python recompiles. Real source still does.
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a = 1\n")
    cap = _cap(deps=["pkg/**"])

    fp1 = fingerprint(cap, tmp_path)
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "a.cpython-314.pyc").write_text("BYTECODE-v1")
    assert fingerprint(cap, tmp_path) == fp1
    (tmp_path / "pkg" / "__pycache__" / "a.cpython-314.pyc").write_text("BYTECODE-v2")
    assert fingerprint(cap, tmp_path) == fp1
    (tmp_path / "pkg" / "a.py").write_text("a = 2\n")
    assert fingerprint(cap, tmp_path) != fp1
