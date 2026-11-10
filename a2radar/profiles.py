"""Profiller: farm/toplama ayarları ve kayıtlı rotalar. Her profil %APPDATA%/A2Radar/profiles/<ad>.json.

Profil içeriği (hepsi isteğe bağlı, eksikse varsayılan):
  attack_skills / buff_skills : [{name, key, interval_s}]   (öneri/uyarı için; oyuna girdi gönderilmez)
  farm_areas   : [{name, x_m, y_m, radius_m, monsters: [ad, ...]}]
  gather       : {resources: [ad, ...], radius_m}
  routes       : {rota_adı: [{x_m, y_m, label}]}
  active_route : seçili rota adı
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_NAME = "Varsayılan"
NAME_RE = re.compile(r"^[\w .()\-ÇçĞğİıÖöŞşÜü]{1,40}$")


def root() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "profiles"


def blank(name: str = DEFAULT_NAME) -> dict:
    return {"name": name, "attack_skills": [], "buff_skills": [], "farm_areas": [],
            "gather": {"resources": [], "radius_m": 150.0}, "routes": {}, "active_route": ""}


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name.strip())) and name.strip() not in (".", "..")


def _clean(d: dict, name: str) -> dict:
    """Bozuk/eksik dosyayı güvenle varsayılanlarla tamamlar."""
    out = blank(name)
    if not isinstance(d, dict):
        return out
    for key in ("attack_skills", "buff_skills", "farm_areas"):
        if isinstance(d.get(key), list):
            out[key] = [x for x in d[key] if isinstance(x, dict)]
    g = d.get("gather")
    if isinstance(g, dict):
        out["gather"]["resources"] = [str(r) for r in g.get("resources", []) if isinstance(r, str)]
        try:
            out["gather"]["radius_m"] = float(g.get("radius_m", 150.0))
        except (TypeError, ValueError):
            pass
    r = d.get("routes")
    if isinstance(r, dict):
        for rn, pts in r.items():
            if isinstance(pts, list):
                out["routes"][str(rn)] = [{"x_m": float(p["x_m"]), "y_m": float(p["y_m"]), "label": str(p.get("label", ""))}
                                          for p in pts if isinstance(p, dict) and "x_m" in p and "y_m" in p]
    out["active_route"] = str(d.get("active_route", "")) if str(d.get("active_route", "")) in out["routes"] else ""
    return out


class ProfileStore:
    def __init__(self, folder=None):
        self.dir = Path(folder) if folder else root()

    def _path(self, name: str) -> Path:
        if not valid_name(name):
            raise ValueError("Profil adı 1-40 karakter olmalı (harf, rakam, boşluk, - . ( ) _).")
        return self.dir / f"{name.strip()}.json"

    def names(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.dir.glob("*.json"))
        except OSError:
            return []

    def load(self, name: str) -> dict:
        try:
            return _clean(json.loads(self._path(name).read_text("utf-8")), name.strip())
        except (OSError, ValueError):
            return blank(name.strip())

    def save(self, prof: dict):
        p = self._path(prof["name"])
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)

    def delete(self, name: str):
        try:
            self._path(name).unlink()
        except OSError:
            pass

    def copy(self, src: str, dst: str) -> dict:
        prof = self.load(src)
        prof["name"] = dst.strip()
        self.save(prof)
        return prof
