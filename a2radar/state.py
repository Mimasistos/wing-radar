from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

MAX_XY, MAX_Z = 500_000.0, 120_000.0   # santimetre
HEADING_MIN_MOVE = 100.0               # 1 m'den kısa hareketten yön çıkarma
TELEPORT_DIST = 3000.0


def valid_coord(x, y, z) -> bool:
    return all(map(math.isfinite, (x, y, z))) and abs(x) <= MAX_XY and abs(y) <= MAX_XY and abs(z) <= MAX_Z


@dataclass
class Cube:
    eid: int
    npc_id: int
    x: float
    y: float
    z: float
    asmodian: bool = False
    seen: float = field(default_factory=time.time)


@dataclass
class Snapshot:
    pos: tuple | None
    heading: float | None
    heading_age: float | None
    pos_age: float | None
    cubes: list
    player_id: int | None
    locked: bool
    packets: int
    last_packet_age: float | None
    connected: bool


class State:
    def __init__(self):
        self._l = threading.Lock()
        self.pos = None
        self.heading = None
        self._anchor = None
        self.pos_time = 0.0
        self.player_id = None
        self.locked = False
        self.char_name = ""
        self.cubes: dict[int, Cube] = {}
        self.packets = 0
        self.packet_time = 0.0
        self.connected = False
        self.on_new_cube = None  # callback(Cube)
        self.calib_until = 0.0
        self.samples: dict[int, list[float]] = {}
        self.heading_time = 0.0

    def touch_packet(self):
        with self._l:
            self.packets += 1
            self.packet_time = time.time()

    def set_player_id(self, eid: int, lock: bool):
        if eid <= 100:
            return
        with self._l:
            if self.locked and not lock:
                return
            self.player_id, self.locked = eid, self.locked or lock

    def set_pos(self, x, y, z):
        if not valid_coord(x, y, z):
            return
        with self._l:
            if self._anchor is None or self.pos is None:
                self._anchor = (x, y)
            else:
                dx, dy = x - self._anchor[0], y - self._anchor[1]
                d = math.hypot(dx, dy)
                if d >= TELEPORT_DIST:
                    self._anchor = (x, y)
                elif d >= HEADING_MIN_MOVE:
                    self.heading = math.atan2(dx, -dy)
                    self.heading_time = time.time()
                    self._anchor = (x, y)
            self.pos, self.pos_time = (x, y, z), time.time()

    # --- kimlik kalibrasyonu: kullanıcı zıplarken z'si en çok değişen varlık = karakter ---
    def start_calibration(self, secs=4.0):
        with self._l:
            self.samples = {}
            self.calib_until = time.time() + secs

    @property
    def calibrating(self):
        return time.time() < self.calib_until

    def sample(self, eid: int, z: float):
        with self._l:
            if time.time() < self.calib_until and valid_coord(0, 0, z):
                self.samples.setdefault(eid, []).append(z)

    def finish_calibration(self):
        """(eid|None, mesaj)"""
        with self._l:
            self.calib_until = 0.0
            seen = len(self.samples)
            rng = sorted(((max(v) - min(v), e) for e, v in self.samples.items() if len(v) >= 3), reverse=True)
            self.samples = {}
        if not rng or rng[0][0] < 60:
            best = f", en büyük z değişimi {rng[0][0]:.0f} cm" if rng else ""
            return None, f"Zıplama algılanmadı ({seen} varlık görüldü{best}). Tekrar dene."
        if len(rng) > 1 and rng[1][0] > rng[0][0] * 0.6:
            return None, f"{len(rng)} oyuncu hareket etti. Tekrar dene."
        self.set_player_id(rng[0][1], lock=True)
        with self._l:
            self.pos = self._anchor = self.heading = None
        return rng[0][1], f"Karakter bulundu (ID {rng[0][1]})"

    def reset_zone(self):
        with self._l:
            self.cubes.clear()
            self.heading = self._anchor = None
            self.pos_time = 0.0

    def add_cube(self, c: Cube):
        with self._l:
            new = c.eid not in self.cubes
            self.cubes[c.eid] = c
        if new and self.on_new_cube:
            self.on_new_cube(c)

    def remove_cube(self, eid: int):
        with self._l:
            self.cubes.pop(eid, None)

    def remove_nearby(self, radius=600.0):
        with self._l:
            if not self.pos:
                return
            px, py, pz = self.pos
            for eid in [e for e, c in self.cubes.items()
                        if (c.x - px) ** 2 + (c.y - py) ** 2 + (c.z - pz) ** 2 <= radius ** 2]:
                del self.cubes[eid]

    def snapshot(self) -> Snapshot:
        now = time.time()
        with self._l:
            return Snapshot(self.pos, self.heading,
                            now - self.heading_time if self.heading_time else None,
                            now - self.pos_time if self.pos_time else None,
                            list(self.cubes.values()), self.player_id, self.locked,
                            self.packets, now - self.packet_time if self.packet_time else None,
                            self.connected)


def nearest(snap: Snapshot, max_m: float):
    """(cube, yatay_mesafe_m, dikey_fark_m, bearing_rad) veya None."""
    if not snap.pos:
        return None
    px, py, pz = snap.pos
    best = None
    for c in snap.cubes:
        d = math.sqrt((c.x - px) ** 2 + (c.y - py) ** 2 + (c.z - pz) ** 2) / 100
        if d <= max_m and (best is None or d < best[1]):
            best = (c, d, (c.z - pz) / 100, math.atan2(c.x - px, -(c.y - py)))
    return best
