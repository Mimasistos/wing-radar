"""Başsız (arayüzsüz) dünya görünümü: yakalama + çözümleme + varlık takibi, ad/tür etiketleriyle.

Farm bot ve diğer araçlar radarın Tk arayüzüne bağlanmadan bunu kullanır:

    from a2radar.world import World
    w = World()
    w.start()
    ...
    v = w.view()
    for s in w.nearest(kind="kaynak", radius_m=150, n=3):
        print(s.name, round(s.dist_m), s.compass, s.dz_m)

Her şey pasif dinlemedir (paket okuma); bu modül oyuna hiçbir girdi göndermez.
Konum güncellemesi yaklaşık 1 Hz gelir: `Seen.age_s` ve `View.pos_age` bayatlığı gösterir, karar veren kod bunu hesaba katmalı.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from . import entities as E
from .state import State


@dataclass(frozen=True)
class Seen:
    eid: int
    npc_id: int
    name: str            # etiket yoksa "NPC <id>"
    kind: str            # canavar | npc | kaynak | diğer | "" (etiketsiz)
    x_m: float
    y_m: float
    z_m: float
    dist_m: float | None     # yatay mesafe (oyuncu konumu yoksa None)
    dz_m: float | None       # yükseklik farkı (+ yukarıda)
    bearing: float | None    # atan2(dx, -dy): 0 = kuzey (oyun -y), saat yönünde
    compass: str
    age_s: float             # son konum güncellemesinden beri
    labeled: bool


@dataclass(frozen=True)
class View:
    pos_m: tuple | None      # (x, y, z) metre
    pos_age: float | None
    heading: float | None
    connected: bool
    player_id: int | None
    entities: tuple          # tuple[Seen]; yakından uzağa (konum yoksa eid sırası)


class World:
    def __init__(self, labels: E.Labels | None = None, state: State | None = None):
        self.state = state or State()
        self.labels = labels or E.Labels()
        self.capture = None
        self.error: str | None = None

    # --- yaşam döngüsü ---
    def start(self):
        if self.capture is not None:
            return
        from .capture import Capture   # scapy/psutil geç import
        self.capture = Capture(self.state, on_error=lambda m: setattr(self, "error", m))
        self.capture.start()

    def stop(self):
        if self.capture is not None:
            self.capture.stop()
            self.capture = None

    # --- okuma ---
    def view(self, radius_m: float | None = None, include_players: bool = False) -> View:
        snap = self.state.snapshot()
        now = time.time()
        pos = snap.pos
        pid = snap.player_id
        out = []
        for e in self.state.entities.snapshot():
            if e.eid == pid or (e.npc_id == -1 and not include_players) or e.npc_id in E.IGNORED:
                continue
            if pos and e.npc_id == 0 and abs(e.x - pos[0]) < 150 and abs(e.y - pos[1]) < 150:
                continue   # kimliği bağlanmamış kendi karakterim
            lab = self.labels.get(e.npc_id)
            dist = dz = bearing = None
            comp = "-"
            if pos:
                dx, dy = e.x - pos[0], e.y - pos[1]
                dist, dz = math.hypot(dx, dy) / 100, (e.z - pos[2]) / 100
                bearing = math.atan2(dx, -dy)
                comp = E.compass(bearing)
                if radius_m is not None and dist > radius_m:
                    continue
            out.append(Seen(e.eid, e.npc_id, lab["name"] if lab else f"NPC {e.npc_id}", lab["kind"] if lab else "",
                            e.x / 100, e.y / 100, e.z / 100, dist, dz, bearing, comp, now - e.updated, lab is not None))
        out.sort(key=lambda s: (s.dist_m is None, s.dist_m or 0.0, s.eid))
        return View(tuple(round(v / 100, 2) for v in pos) if pos else None, snap.pos_age, snap.heading,
                    snap.connected, pid, tuple(out))

    def nearest(self, kind: str | None = None, name: str | None = None, npc_id: int | None = None,
                radius_m: float | None = None, n: int = 1) -> list[Seen]:
        """Yakından uzağa süzülmüş varlıklar. name büyük/küçük harf duyarsız tam eşleşme."""
        res = []
        for s in self.view(radius_m).entities:
            if kind and s.kind != kind:
                continue
            if name and s.name.lower() != name.lower():
                continue
            if npc_id is not None and s.npc_id != npc_id:
                continue
            res.append(s)
            if len(res) >= n:
                break
        return res

    def counts(self, radius_m: float | None = None) -> dict[str, int]:
        """Ad -> adet (alan bekçisi / canavar sayımı için)."""
        c: dict[str, int] = {}
        for s in self.view(radius_m).entities:
            c[s.name] = c.get(s.name, 0) + 1
        return c
