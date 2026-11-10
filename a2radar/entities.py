"""Oyun varlıkları (canavar/NPC): spawn, hareket, despawn takibi ve kullanıcı etiketleri.

Canlı oyunda doğrulanan şema (2026-11-10, Şema taraması):
  13876 canavar/NPC spawn: eid(varint) +2 npc_id(u32) +6 konum(<fff)
  13889 canavar spawn (ikinci biçim): eid +3 npc_id(u32) +9 konum (Coradon'larla eşleşti; aynı tür = aynı kimlik)
  14106 hareket: eid +2 konum; 14107/14108 hareket: eid +1 konum; 14109 küçük delta paketi (çözülmedi)
  13877/13882/13890 despawn
Spawn görülmemiş ama hareket eden varlıklar npc_id=0 olarak izlenir.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

KINDS = ("canavar", "npc", "kaynak", "diğer")
COMPASS = ("K", "KD", "D", "GD", "G", "GB", "B", "KB")     # kuzey = oyun -y yönü


def compass(bearing: float) -> str:
    """bearing: atan2(dx, -dy) (0 = kuzey, saat yönünde)."""
    return COMPASS[int(round((math.degrees(bearing) % 360) / 45)) % 8]


@dataclass
class Entity:
    eid: int
    npc_id: int
    x: float
    y: float
    z: float
    op: int = 0
    spawned: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)


class EntityTracker:
    MAX_ENTITIES = 5000
    MAX_SAMPLES = 300
    LOG_MAX = 1000

    def __init__(self):
        self._l = threading.Lock()
        self.by_eid: dict[int, Entity] = {}
        self.samples: dict[int, dict] = {}          # npc_id -> ilk görülen ham spawn yükü (tanı için)
        self.log: list[dict] = []                   # her spawn paketi (tanı; en çok LOG_MAX)
        self.on_spawn = None                        # geri çağrı(npc_id, x, y, z): spawn veritabanı için

    @staticmethod
    def _origin(x, y, z) -> bool:
        """(0,0,0): geçersiz/boş konum (ör. oyuncu spawn'ında ya da yanlış ofsetten okunan çöp)."""
        return abs(x) + abs(y) + abs(z) < 1.0

    def spawn(self, eid: int, npc_id: int, x: float, y: float, z: float, op: int = 0, raw: bytes = b""):
        if self._origin(x, y, z):
            return
        with self._l:
            if len(self.by_eid) >= self.MAX_ENTITIES and eid not in self.by_eid:
                oldest = min(self.by_eid.values(), key=lambda e: e.updated)
                del self.by_eid[oldest.eid]
            self.by_eid[eid] = Entity(eid, npc_id, x, y, z, op)
            if len(self.log) < self.LOG_MAX:
                self.log.append({"t": round(time.time(), 1), "event": "spawn", "op": op, "eid": eid, "npc": npc_id,
                                 "pos_m": [round(x / 100), round(y / 100), round(z / 100)], "hex": raw[:64].hex()})
            if npc_id not in self.samples and len(self.samples) < self.MAX_SAMPLES:
                self.samples[npc_id] = {"op": op, "hex": raw[:48].hex(), "pos": [round(x, 1), round(y, 1), round(z, 1)]}
        cb = self.on_spawn
        if cb is not None and npc_id > 0:
            try:
                cb(npc_id, x, y, z)
            except Exception:
                pass

    def move(self, eid: int, x: float, y: float, z: float):
        if self._origin(x, y, z):
            return
        with self._l:
            e = self.by_eid.get(eid)
            if e:
                e.x, e.y, e.z, e.updated = x, y, z, time.time()
            elif eid > 100 and len(self.by_eid) < self.MAX_ENTITIES:
                # spawn paketi görülmemiş ama hareket ediyor: kimliği bilinmeyen (npc 0) varlık olarak izle
                self.by_eid[eid] = Entity(eid, 0, x, y, z, 0)

    def despawn(self, eid: int, op: int = 0):
        with self._l:
            e = self.by_eid.pop(eid, None)
            if len(self.log) < self.LOG_MAX:
                self.log.append({"t": round(time.time(), 1), "event": "despawn", "op": op, "eid": eid,
                                 "npc": e.npc_id if e else None, "tracked": e is not None,
                                 "pos_m": [round(e.x / 100), round(e.y / 100), round(e.z / 100)] if e else None})

    def clear(self):
        with self._l:
            self.by_eid.clear()

    def snapshot(self) -> list[Entity]:
        with self._l:
            return [replace(e) for e in self.by_eid.values()]


def group_entities(entities, pos, radius_m: float | None = None):
    """npc_id'ye göre grupla: [{npc_id, count, nearest_m, bearing, dz_m}], en yakın önce. pos yoksa mesafe None."""
    groups: dict[int, dict] = {}
    for e in entities:
        dist = bearing = dz = None
        if pos:
            dx, dy = e.x - pos[0], e.y - pos[1]
            dist = math.hypot(dx, dy) / 100
            bearing, dz = math.atan2(dx, -dy), (e.z - pos[2]) / 100
            if radius_m is not None and dist > radius_m:
                continue
        g = groups.setdefault(e.npc_id, {"npc_id": e.npc_id, "count": 0, "nearest_m": None, "bearing": None, "dz_m": None})
        g["count"] += 1
        if dist is not None and (g["nearest_m"] is None or dist < g["nearest_m"]):
            g["nearest_m"], g["bearing"], g["dz_m"] = dist, bearing, dz
    return sorted(groups.values(), key=lambda g: (g["nearest_m"] is None, g["nearest_m"] or 0.0, g["npc_id"]))


# Tranein Highland, 2026-11-10: ekrandaki oyun içi adlar ve adetlerle birebir eşleştirildi.
# 2000929 (Faded Papis) kullanıcı tahmini: hareketli, kısa ömürlü; kesin değil.
KNOWN = {
    # canavarlar
    2400914: {"name": "Brax", "kind": "canavar"},
    2400905: {"name": "Kailin", "kind": "canavar"},
    2400906: {"name": "Young Kailin", "kind": "canavar"},
    2400907: {"name": "Aberrant Coradon", "kind": "canavar"},   # ekran görüntüsünde adet/mesafe ile doğrulandı
    2400908: {"name": "Young Coradon", "kind": "canavar"},
    # Aynı canavar farklı bölgelerde farklı kimlikle gelebilir: aynı ad = katalogda tek satır
    2000929: {"name": "Faded Papis", "kind": "canavar"},
    2000927: {"name": "Faded Papis", "kind": "canavar"},
    2000925: {"name": "Rafflesia", "kind": "canavar"},
    2400249: {"name": "Withered Armadon", "kind": "canavar"},
    2400143: {"name": "Outpost Probe", "kind": "canavar"},
    2400116: {"name": "Dratona Tinkerer", "kind": "canavar"},
    2400117: {"name": "Dratona Watcher", "kind": "canavar"},
    2400106: {"name": "Draconute Ranger", "kind": "canavar"},
    2400107: {"name": "Draconute Superior Combatant", "kind": "canavar"},
    2400102: {"name": "Draconute Tinkerer", "kind": "canavar"},
    2400108: {"name": "Varg Warrior", "kind": "canavar"},
    2400112: {"name": "Draconute Superior Warrior", "kind": "canavar"},
    2400113: {"name": "Drakan Warden", "kind": "canavar"},
    # yapılar: avlanmaz, listelerde görünmez ("diğer"); adları yine de tabloda ve etiketlerde okunur
    2400115: {"name": "Balaur Elite Combat Flag", "kind": "diğer"},
    2400121: {"name": "Balaur Combat Flag", "kind": "diğer"},
    2400122: {"name": "Sanctum Watchtower", "kind": "diğer"},
    2400230: {"name": "Ashen Spider", "kind": "canavar"},
    # kaynaklar (sabit spawn noktaları)
    1550000: {"name": "Odyle", "kind": "kaynak"},
    1550004: {"name": "Orichalcum", "kind": "kaynak"},
    1550020: {"name": "Sapphire", "kind": "kaynak"},
    1550033: {"name": "Calendula", "kind": "kaynak"},
    1550040: {"name": "Raydum", "kind": "kaynak"},
}


# Listelerde, haritada ve asistanda hiç gösterilmeyen kimlikler (kullanıcı kararı)
IGNORED = {
    1300865: "Empyrean Trace: Altgard",     # bölge kaynağı, işe yaramıyor
    1300014: "önemsiz (kullanıcı kararı)",
    1300019: "önemsiz (kullanıcı kararı)",
    1300020: "önemsiz (kullanıcı kararı)",
    1100001: "önemsiz (kullanıcı kararı)",
    1300007: "önemsiz (kullanıcı kararı)",
    1300006: "önemsiz (kullanıcı kararı)",
    1300005: "önemsiz (kullanıcı kararı)",
    1300311: "önemsiz (kullanıcı kararı)",
    1300310: "önemsiz (kullanıcı kararı)",
    1300413: "önemsiz (kullanıcı kararı)",
    1400277: "önemsiz (kullanıcı kararı)",
    1300008: "önemsiz (kullanıcı kararı)",
    1300003: "önemsiz (kullanıcı kararı)",
    1400330: "önemsiz (kullanıcı kararı)",
    2400031: "önemsiz (kullanıcı kararı)",
    1400276: "önemsiz (kullanıcı kararı)",
    1100006: "önemsiz (kullanıcı kararı)",
}


class Labels:
    """npc_id -> {name, kind}. %APPDATA%/A2Radar/entities.json."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "entities.json"
        self.data: dict[int, dict] = {}
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            self.data = {int(k): {"name": str(v["name"]), "kind": v.get("kind", "canavar")}
                         for k, v in raw.items() if isinstance(v, dict) and "name" in v}
        except (OSError, ValueError, TypeError, KeyError):
            self.data = {}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({str(k): v for k, v in sorted(self.data.items())}, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def get(self, npc_id: int):
        """Önce kullanıcı etiketi, yoksa yerleşik (canlı oyunda ekrandaki adlarla doğrulanan) kimlik."""
        return self.data.get(npc_id) or KNOWN.get(npc_id)

    def set(self, npc_id: int, name: str, kind: str = "canavar"):
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("Ad 1-60 karakter olmalı.")
        if kind not in KINDS:
            raise ValueError("Tür: " + ", ".join(KINDS))
        self.data[int(npc_id)] = {"name": name, "kind": kind}
        self.save()

    def remove(self, npc_id: int):
        if self.data.pop(int(npc_id), None) is not None:
            self.save()


def export_samples(tracker: EntityTracker, labels: Labels, path=None, pos=None) -> Path:
    """Tanı: npc_id başına ilk ham spawn yükü + etiket. Şemayı doğrulamak için paylaşılabilir."""
    p = Path(path) if path else Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "spawn_samples.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {str(k): {**v, "label": (labels.get(k) or {}).get("name")} for k, v in sorted(tracker.samples.items())}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    # her spawn + şu an izlenen varlıklar + oyuncu konumu: ekrandaki canavarlarla eşleştirmek için
    now = [{"eid": e.eid, "npc": e.npc_id, "op": e.op, "pos_m": [round(e.x / 100), round(e.y / 100), round(e.z / 100)],
            "age_s": round(time.time() - e.spawned), "moved_s_ago": round(time.time() - e.updated)}
           for e in tracker.snapshot()]
    ppos = [round(v / 100) for v in pos] if pos else None
    (p.parent / "spawn_log.json").write_text(
        json.dumps({"player_pos_m": ppos, "tracked": now, "spawn_log": list(tracker.log)}, ensure_ascii=False, indent=1), "utf-8")
    return p
