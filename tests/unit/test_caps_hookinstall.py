import json
from pathlib import Path

import pytest
from caps.hookinstall import install_hook, uninstall_hook, HOOK_TAG, PONYTAIL_TAG


@pytest.mark.unit
def test_install_into_empty_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    data = json.loads(settings.read_text())
    stops = data["hooks"]["Stop"]
    assert any(h.get("hooks", [{}])[0].get("command") == "/x/caps-stop-gate.sh"
               for h in stops)
    assert list(tmp_path.glob("settings.json.bak.*"))


@pytest.mark.unit
def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    stops = json.loads(settings.read_text())["hooks"]["Stop"]
    ours = [h for h in stops if h.get("_caps") == HOOK_TAG]
    assert len(ours) == 1


@pytest.mark.unit
def test_install_preserves_existing_hooks_and_keys(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "env": {"A": "1"},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]},
    }))
    install_hook(settings, command="/x/caps-stop-gate.sh")
    data = json.loads(settings.read_text())
    assert data["env"] == {"A": "1"}
    cmds = [h["hooks"][0]["command"] for h in data["hooks"]["Stop"]]
    assert "/other.sh" in cmds and "/x/caps-stop-gate.sh" in cmds


@pytest.mark.unit
def test_uninstall_removes_only_ours(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]},
    }))
    install_hook(settings, command="/x/caps-stop-gate.sh")
    uninstall_hook(settings)
    cmds = [h["hooks"][0]["command"] for h in json.loads(settings.read_text())["hooks"]["Stop"]]
    assert cmds == ["/other.sh"]


@pytest.mark.unit
def test_install_ponytail_session_start_hook_with_matcher(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-ponytail.sh",
                 event="SessionStart", tag=PONYTAIL_TAG,
                 matcher="startup|resume|clear|compact")
    starts = json.loads(settings.read_text())["hooks"]["SessionStart"]
    ours = [h for h in starts if h.get("_caps") == PONYTAIL_TAG]
    assert len(ours) == 1
    assert ours[0]["matcher"] == "startup|resume|clear|compact"
    assert ours[0]["hooks"][0]["command"] == "/x/caps-ponytail.sh"


@pytest.mark.unit
def test_stop_gate_and_ponytail_coexist_and_uninstall_independently(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    install_hook(settings, command="/x/caps-stop-gate.sh")
    install_hook(settings, command="/x/caps-ponytail.sh",
                 event="SessionStart", tag=PONYTAIL_TAG,
                 matcher="startup")
    # Removing the posture hook must leave the Stop gate untouched.
    uninstall_hook(settings, event="SessionStart", tag=PONYTAIL_TAG)
    data = json.loads(settings.read_text())
    assert data["hooks"]["SessionStart"] == []
    assert any(h.get("_caps") == HOOK_TAG for h in data["hooks"]["Stop"])
