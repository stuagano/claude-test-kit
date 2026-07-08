import pytest

from caps.backup import backup_file


@pytest.mark.unit
def test_backup_copies_content(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("hello")
    bak = backup_file(f)
    assert bak.exists()
    assert bak.read_text() == "hello"
    assert bak.name.startswith("settings.json.bak.")


@pytest.mark.unit
def test_backup_does_not_overwrite_same_day(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("a")
    b1 = backup_file(f)
    f.write_text("b")
    b2 = backup_file(f)
    assert b1 != b2
    assert b1.read_text() == "a"
    assert b2.read_text() == "b"
