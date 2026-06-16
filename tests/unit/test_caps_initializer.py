from pathlib import Path

import pytest

from caps import initializer
from caps.manifest import load_manifest


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


@pytest.mark.unit
def test_vendor_refuses_to_target_the_kit_itself(tmp_path):
    kit = tmp_path / "kit"
    (kit / "ctk").mkdir(parents=True)
    (kit / "ctk" / "mod.py").write_text("x = 1\n")
    with pytest.raises(ValueError):
        initializer.vendor_framework(kit, kit, force=True)


@pytest.mark.unit
def test_ensure_conftest_copies_when_absent(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "conftest.py").write_text("# kit conftest\n")
    target = tmp_path / "proj"
    target.mkdir()

    r = initializer.ensure_conftest(target, kit)

    assert (target / "conftest.py").read_text() == "# kit conftest\n"
    assert r.action == "created"


@pytest.mark.unit
def test_ensure_conftest_warns_loudly_when_present(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "conftest.py").write_text("# kit conftest\n")
    target = tmp_path / "proj"
    target.mkdir()
    (target / "conftest.py").write_text("# user's own conftest\n")

    r = initializer.ensure_conftest(target, kit)

    assert (target / "conftest.py").read_text() == "# user's own conftest\n"  # untouched
    assert r.action == "warned"
    assert "error-log guard is OFF" in r.detail


@pytest.mark.unit
def test_ensure_pytest_config_writes_when_absent(tmp_path):
    r = initializer.ensure_pytest_config(tmp_path)
    ini = (tmp_path / "pytest.ini").read_text()
    assert "pythonpath = ." in ini
    assert "integration:" in ini and "allow_error_logs:" in ini
    assert r.action == "created"


@pytest.mark.unit
def test_ensure_pytest_config_skips_when_pytest_ini_present(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    r = initializer.ensure_pytest_config(tmp_path)
    assert r.action == "skipped"


@pytest.mark.unit
def test_ensure_pytest_config_detects_pyproject_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = ['.']\n"
    )
    r = initializer.ensure_pytest_config(tmp_path)
    assert r.action == "skipped"
    assert not (tmp_path / "pytest.ini").exists()


@pytest.mark.unit
def test_ensure_pytest_config_detects_setup_cfg_section(tmp_path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\nmarkers =\n    unit\n")
    r = initializer.ensure_pytest_config(tmp_path)
    assert r.action == "skipped"
    assert not (tmp_path / "pytest.ini").exists()


@pytest.mark.unit
def test_starter_manifest_written_and_parses_empty(tmp_path):
    results = initializer.ensure_starter_manifest(tmp_path)

    manifest = tmp_path / "capabilities.yaml"
    assert manifest.is_file()
    assert load_manifest(manifest) == []          # parses, zero capabilities
    assert (tmp_path / "checks" / ".gitkeep").is_file()
    assert any(r.action == "created" and r.target.endswith("capabilities.yaml")
               for r in results)


@pytest.mark.unit
def test_starter_manifest_never_overwrites_existing(tmp_path):
    (tmp_path / "capabilities.yaml").write_text("capabilities:\n  - mine\n")
    results = initializer.ensure_starter_manifest(tmp_path)
    assert (tmp_path / "capabilities.yaml").read_text() == "capabilities:\n  - mine\n"
    assert any(r.action == "skipped" and r.target.endswith("capabilities.yaml")
               for r in results)


@pytest.mark.unit
def test_gitignore_created_with_all_entries_when_absent(tmp_path):
    r = initializer.ensure_gitignore(tmp_path)
    text = (tmp_path / ".gitignore").read_text()
    for entry in (".venv/", "__pycache__/", ".pytest_cache/", "*.bak.*"):
        assert entry in text
    assert ".ctk/" not in text  # the ledger is meant to be committed
    assert r.action == "created"


@pytest.mark.unit
def test_gitignore_appends_only_missing_entries(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n.venv/\n")
    r = initializer.ensure_gitignore(tmp_path)
    text = (tmp_path / ".gitignore").read_text()
    assert text.count(".venv/") == 1            # not duplicated
    assert "node_modules/" in text              # user's lines preserved
    assert "*.bak.*" in text                    # missing one added
    assert r.action == "created"                # "created" = we added entries


@pytest.mark.unit
def test_gitignore_noop_when_all_present(tmp_path):
    (tmp_path / ".gitignore").write_text(
        ".venv/\n__pycache__/\n.pytest_cache/\n*.bak.*\n"
    )
    before = (tmp_path / ".gitignore").read_text()
    r = initializer.ensure_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == before
    assert r.action == "skipped"


@pytest.mark.unit
def test_pyyaml_step_instructs_by_default_and_does_not_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(initializer, "_pip_install", lambda pkg: calls.append(pkg))

    r = initializer.maybe_install_pyyaml(install_deps=False)

    assert r.action == "instructed"
    assert "PyYAML" in r.detail
    assert calls == []  # env never mutated without opt-in


@pytest.mark.unit
def test_pyyaml_step_installs_on_opt_in(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(initializer, "_pip_install", lambda pkg: calls.append(pkg))

    r = initializer.maybe_install_pyyaml(install_deps=True)

    assert r.action == "installed"
    assert calls == ["PyYAML"]


@pytest.mark.unit
def test_init_project_lays_down_everything_in_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(initializer, "_pip_install", lambda pkg: None)
    target = tmp_path / "proj"
    target.mkdir()
    kit = initializer.kit_root()  # vendor the real kit

    results = initializer.init_project(target, kit=kit, force=False, install_deps=False)

    for d in ("ctk", "caps", "bin"):
        assert (target / d).is_dir()
        assert not (target / d / "__pycache__").exists()
    assert (target / "conftest.py").is_file()
    assert (target / "pytest.ini").is_file()
    assert (target / "capabilities.yaml").is_file()
    assert (target / "checks" / ".gitkeep").is_file()
    assert ".venv/" in (target / ".gitignore").read_text()
    assert all(isinstance(r, initializer.StepResult) for r in results)
    assert any(r.action == "instructed" for r in results)  # the PyYAML line
