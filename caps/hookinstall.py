from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .backup import backup_file

HOOK_TAG = "caps-stop-gate"


def _entry(command: str) -> dict:
    return {"_caps": HOOK_TAG,
            "hooks": [{"type": "command", "command": command, "timeout": 10}]}


def install_hook(settings_path: Union[str, Path], command: str) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}") if settings_path.exists() else {}
    if settings_path.exists():
        backup_file(settings_path)
    hooks = data.setdefault("hooks", {})
    stops = hooks.setdefault("Stop", [])
    stops[:] = [h for h in stops if h.get("_caps") != HOOK_TAG]   # idempotent
    stops.append(_entry(command))
    settings_path.write_text(json.dumps(data, indent=2) + "\n")


def uninstall_hook(settings_path: Union[str, Path]) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}") if settings_path.exists() else {}
    if settings_path.exists():
        backup_file(settings_path)
    stops = data.get("hooks", {}).get("Stop", [])
    data.setdefault("hooks", {})["Stop"] = [h for h in stops if h.get("_caps") != HOOK_TAG]
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
