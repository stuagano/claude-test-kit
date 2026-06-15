from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

HOOK_TAG = "caps-stop-gate"


def _backup(path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    candidate = path.with_suffix(path.suffix + f".bak.{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak.{stamp}-{n}")
        n += 1
    shutil.copy2(path, candidate)


def _entry(command: str) -> dict:
    return {"_caps": HOOK_TAG,
            "hooks": [{"type": "command", "command": command, "timeout": 10}]}


def install_hook(settings_path: Union[str, Path], command: str) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}") if settings_path.exists() else {}
    if settings_path.exists():
        _backup(settings_path)
    hooks = data.setdefault("hooks", {})
    stops = hooks.setdefault("Stop", [])
    stops[:] = [h for h in stops if h.get("_caps") != HOOK_TAG]   # idempotent
    stops.append(_entry(command))
    settings_path.write_text(json.dumps(data, indent=2) + "\n")


def uninstall_hook(settings_path: Union[str, Path]) -> None:
    settings_path = Path(settings_path)
    data = json.loads(settings_path.read_text() or "{}") if settings_path.exists() else {}
    if settings_path.exists():
        _backup(settings_path)
    stops = data.get("hooks", {}).get("Stop", [])
    data.setdefault("hooks", {})["Stop"] = [h for h in stops if h.get("_caps") != HOOK_TAG]
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
