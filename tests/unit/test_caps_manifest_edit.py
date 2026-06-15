import pytest
from caps.manifest import load_manifest
from caps.manifest_edit import add_capability, ManifestEditError


def _common():
    return dict(description="writes rows and reads back", given="a db",
                when="the job runs", then="rows read back", tier="cheap")


@pytest.mark.unit
def test_creates_manifest_with_header_when_absent(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap1", deps=["ingest.py"],
                   check="checks/test_cap1.py::test_cap1", **_common())
    assert m.exists()
    caps = load_manifest(m)
    assert [c.id for c in caps] == ["cap1"]
    assert caps[0].check_kind == "pytest"


@pytest.mark.unit
def test_appends_and_preserves_comments_and_prior_entries(tmp_path):
    m = tmp_path / "capabilities.yaml"
    m.write_text(
        "# my hand-written note\n"
        "capabilities:\n"
        "  - id: existing\n"
        "    description: d\n"
        "    given: g\n"
        "    when: w\n"
        "    then: t\n"
        "    tier: cheap\n"
        "    deps: []\n"
        "    check: checks/a.py::t\n"
    )
    add_capability(m, id="cap2", deps=[],
                   check="checks/test_cap2.py::test_cap2", **_common())
    text = m.read_text()
    assert "# my hand-written note" in text
    ids = [c.id for c in load_manifest(m)]
    assert ids == ["existing", "cap2"]


@pytest.mark.unit
def test_duplicate_id_rejected_and_file_unchanged(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="dup", deps=[], check="checks/t.py::t", **_common())
    before = m.read_text()
    with pytest.raises(ManifestEditError):
        add_capability(m, id="dup", deps=[], check="checks/t2.py::t", **_common())
    assert m.read_text() == before


@pytest.mark.unit
def test_non_block_style_rejected_and_file_unchanged(tmp_path):
    m = tmp_path / "capabilities.yaml"
    m.write_text("capabilities: []\n")
    before = m.read_text()
    with pytest.raises(ManifestEditError):
        add_capability(m, id="x", deps=[], check="checks/t.py::t", **_common())
    assert m.read_text() == before


@pytest.mark.unit
def test_scaffolds_failing_stub_when_check_file_absent(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap3", deps=[],
                   check="checks/test_cap3.py::test_cap3", **_common())
    stub = tmp_path / "checks" / "test_cap3.py"
    assert stub.exists()
    body = stub.read_text()
    assert "def test_cap3" in body
    assert "NotImplementedError" in body


@pytest.mark.unit
def test_does_not_overwrite_existing_check_file(tmp_path):
    m = tmp_path / "capabilities.yaml"
    (tmp_path / "checks").mkdir()
    real = tmp_path / "checks" / "test_cap4.py"
    real.write_text("def test_cap4():\n    assert True  # real check\n")
    add_capability(m, id="cap4", deps=[],
                   check="checks/test_cap4.py::test_cap4", **_common())
    assert "real check" in real.read_text()


@pytest.mark.unit
def test_shell_check_appended_without_scaffold(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap5", deps=[], shell="./prove.sh", **_common())
    cap = load_manifest(m)[0]
    assert cap.check_kind == "shell"
    assert cap.check_target == "./prove.sh"
    assert not (tmp_path / "prove.sh").exists()


@pytest.mark.unit
def test_backup_written_when_manifest_existed(tmp_path):
    m = tmp_path / "capabilities.yaml"
    add_capability(m, id="cap6", deps=[], check="checks/t.py::t", **_common())
    add_capability(m, id="cap7", deps=[], check="checks/t7.py::t", **_common())
    assert list(tmp_path.glob("capabilities.yaml.bak.*"))


@pytest.mark.unit
def test_requires_exactly_one_of_check_or_shell(tmp_path):
    m = tmp_path / "capabilities.yaml"
    with pytest.raises(ManifestEditError):
        add_capability(m, id="z", deps=[], **_common())
    with pytest.raises(ManifestEditError):
        add_capability(m, id="z", deps=[], check="a::b", shell="x", **_common())
