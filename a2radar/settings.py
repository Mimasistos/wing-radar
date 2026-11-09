from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {"alpha": 0.9, "scale": 1.0, "sound": True, "volume": 0.7,
            "profile": "Radar Ping", "char_name": "", "range_m": 150.0,
            "x": 40, "y": 40, "route_on": True, "scan_m": 100.0,
            "minimap_on": True, "map_m": 300.0, "land_only": False}


def path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "settings.json"


def load() -> dict:
    d = dict(DEFAULTS)
    try:
        raw = json.loads(path().read_text("utf-8"))
        d.update({k: raw[k] for k in DEFAULTS
                  if k in raw and isinstance(raw[k], type(DEFAULTS[k]) if type(DEFAULTS[k]) is not float else (int, float))})
    except (OSError, ValueError):
        pass
    return d


def save(d: dict):
    try:
        path().parent.mkdir(parents=True, exist_ok=True)
        tmp = path().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2), "utf-8")
        tmp.replace(path())
    except OSError:
        pass
