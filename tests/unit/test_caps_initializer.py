from pathlib import Path

import pytest

from caps import initializer


@pytest.mark.unit
def test_vendor_copies_dirs_excluding_pycache(tmp_path):
    # Fake a kit with the three framework dirs + a stray __pycache__/.pyc.
    kit = tmp_path / "kit"
    for d in ("ctk", "caps", "bin"):
        (kit / d).mkdir(parents=True)
        (kit / d / "mod.py").write_text("x = 1\n")
        (kit / d / "__pycache__").mkdir()
        (kit / d / "__pycache__" / "mod.pyc").write_text("junk")
    target = tmp_path / "proj"
    target.mkdir()

    results = initializer.vendor_framework(target, kit, force=False)

    for d in ("ctk", "caps", "bin"):
        assert (target / d / "mod.py").is_file()
        assert not (target / d / "__pycache__").exists(), "must not vendor build artifacts"
    assert {r.action for r in results} == {"created"}


@pytest.mark.unit
def test_vendor_skips_existing_without_force_overwrites_with_force(tmp_path):
    kit = tmp_path / "kit"
    (kit / "ctk").mkdir(parents=True)
    (kit / "ctk" / "mod.py").write_text("new = 2\n")
    (kit / "caps").mkdir(); (kit / "caps" / "mod.py").write_text("c = 1\n")
    (kit / "bin").mkdir(); (kit / "bin" / "g.sh").write_text("echo\n")
    target = tmp_path / "proj"
    (target / "ctk").mkdir(parents=True)
    (target / "ctk" / "mod.py").write_text("old = 1\n")  # pre-existing

    skipped = initializer.vendor_framework(target, kit, force=False)
    assert (target / "ctk" / "mod.py").read_text() == "old = 1\n"
    assert any(r.action == "skipped" and r.target.endswith("ctk") for r in skipped)

    forced = initializer.vendor_framework(target, kit, force=True)
    assert (target / "ctk" / "mod.py").read_text() == "new = 2\n"
    assert any(r.action == "overwritten" and r.target.endswith("ctk") for r in forced)
