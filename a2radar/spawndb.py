"""Spawn noktası veritabanı: canlı gözlemlenen her canavar/kaynak spawn'ı kalıcı olarak birikir.

Hazır bir veri kaynağı yok; bölgede gezdikçe öğrenilir. Aynı NPC türünün 4 m içindeki spawn'ları tek
nokta sayılır ve sayacı artar (sabit spawn noktaları yüksek sayı alır, dolaşan canavarlar dağınık kalır).
Dosya: %APPDATA%/A2Radar/spawns.json. Koordinatlar oyun santimetresi.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

from . import opcodes
from .entities import IGNORED


def default_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "spawns.json"


class SpawnDB:
    DEDUP_CM = 400.0
    MAX_PER_NPC = 400
    SAVE_EVERY = 10.0

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._l = threading.Lock()
        self.pts: dict[int, list[list]] = {}      # npc_id -> [[x, y, z, sayı, son_görülme], ...]
        self.dirty = False
        self._last_save = 0.0
        self.load()

    # --- kalıcılık ---
    def load(self):
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            self.pts = {int(k): [[float(p[0]), float(p[1]), float(p[2]), int(p[3]), float(p[4])] for p in v]
                        for k, v in raw.items()}
        except (OSError, ValueError, TypeError, IndexError):
            self.pts = {}

    def flush(self, force: bool = False):
        now = time.time()
        with self._l:
            if not self.dirty or (not force and now - self._last_save < self.SAVE_EVERY):
                return
            data = {str(k): [[round(p[0], 1), round(p[1], 1), round(p[2], 1), p[3], round(p[4])] for p in v]
                    for k, v in sorted(self.pts.items())}
            self.dirty = False
            self._last_save = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(self.path)
        except OSError:
            with self._l:
                self.dirty = True

    # --- kayıt ---
    def record(self, npc_id: int, x: float, y: float, z: float):
        """EntityTracker.on_spawn geri çağrısı. Küpler ve geçersiz kimlikler kaydedilmez."""
        if npc_id <= 0 or opcodes.is_cube(npc_id) or npc_id in IGNORED:
            return
        now = time.time()
        with self._l:
            lst = self.pts.setdefault(npc_id, [])
            best, bd = None, self.DEDUP_CM
            for p in lst:
                d = math.hypot(p[0] - x, p[1] - y)
                if d < bd:
                    best, bd = p, d
            if best is not None:
                best[3] += 1
                best[4] = now
            elif len(lst) < self.MAX_PER_NPC:
                lst.append([x, y, z, 1, now])
            else:
                return
            self.dirty = True
        self.flush()

    # --- okuma ---
    def npc_ids(self) -> list[int]:
        with self._l:
            return sorted(self.pts)

    def count(self, npc_id: int) -> int:
        with self._l:
            return len(self.pts.get(npc_id, ()))

    def points(self, npc_ids) -> list[tuple]:
        """[(x, y, z, sayı, npc_id)] santimetre."""
        out = []
        with self._l:
            for n in npc_ids:
                out += [(p[0], p[1], p[2], p[3], n) for p in self.pts.get(n, ())]
        return out

    # --- içe aktarma ---
    def import_log(self, path) -> int:
        """Ham kayıt dışa aktarımından (spawn_log.json) spawn olaylarını alır; metreye yuvarlandığı için kabadır."""
        try:
            raw = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError):
            return 0
        n = 0
        for e in raw.get("spawn_log", []) if isinstance(raw, dict) else []:
            try:
                if e.get("event") == "spawn" and e.get("npc", 0) > 0 and e.get("pos_m"):
                    x, y, z = (float(v) * 100 for v in e["pos_m"])
                    if abs(x) + abs(y) > 1000:
                        self.record(int(e["npc"]), x, y, z)
                        n += 1
            except (TypeError, ValueError):
                continue
        self.flush(force=True)
        return n
