import textwrap
import pytest
from caps.manifest import load_manifest, ManifestError


def _write(tmp_path, body: str):
    p = tmp_path / "capabilities.yaml"
    p.write_text(textwrap.dedent(body))
    return p


@pytest.mark.unit
def test_loads_pytest_check_with_defaults(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: writes-db
            description: writes rows and reads them back
            given: a reachable db
            when: the job runs
            then: rows read back
            tier: live
            deps: [ingest.py]
            check: checks/test_db.py::test_write_readback
    """)
    caps = load_manifest(p)
    assert len(caps) == 1
    c = caps[0]
    assert c.id == "writes-db"
    assert c.tier == "live"
    assert c.deps == ["ingest.py"]
    assert c.check_kind == "pytest"
    assert c.check_target == "checks/test_db.py::test_write_readback"
    assert c.freshness == "24h"
    assert c.warnings == []


@pytest.mark.unit
def test_cheap_default_freshness_is_code(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: parses-output
            description: parses
            given: a file
            when: parse runs
            then: structured output
            tier: cheap
            deps: ["src/**"]
            check: checks/test_parse.py::test_it
    """)
    assert load_manifest(p)[0].freshness == "code"


@pytest.mark.unit
def test_shell_check(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: deploy-live
            description: deploy is live
            given: creds
            when: deploy runs
            then: app responds
            tier: live
            deps: [app.yaml]
            check:
              shell: ./scripts/prove_deploy.sh app
    """)
    c = load_manifest(p)[0]
    assert c.check_kind == "shell"
    assert c.check_target == "./scripts/prove_deploy.sh app"


@pytest.mark.unit
def test_missing_deps_produces_warning(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: no-deps
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/test_x.py::test_x
    """)
    c = load_manifest(p)[0]
    assert c.deps == []
    assert any("deps" in w for w in c.warnings)


@pytest.mark.unit
def test_bad_tier_raises(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: bad
            description: x
            given: g
            when: w
            then: t
            tier: medium
            check: checks/test_x.py::test_x
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)


@pytest.mark.unit
def test_duplicate_ids_raise(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: dup
            description: x
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/a.py::t
          - id: dup
            description: y
            given: g
            when: w
            then: t
            tier: cheap
            check: checks/b.py::t
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)


@pytest.mark.unit
def test_missing_required_field_raises(tmp_path):
    p = _write(tmp_path, """
        capabilities:
          - id: incomplete
            description: x
            tier: cheap
            check: checks/x.py::t
    """)
    with pytest.raises(ManifestError):
        load_manifest(p)
