from __future__ import annotations

import math
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .entities import EntityTracker

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
        self.entities = EntityTracker()      # tüm NPC/canavar varlıkları (küpler de dahil)
        self._jump_cand = None
        self.diag: Counter = Counter()       # akış tanısı: gap, stall, decode_error, feed_error
        self.last_decode_error = ""
        self.scan = None                     # schemascan.SchemaScan (yalnız tarama sırasında)
        self.player_names: dict[int, str] = {}   # eid -> oyuncu adı (13889 oyuncu spawn paketlerinden)
        self.learner = None                 # statlearn.StatLearner (yalnız değer ararken)
        self.statmap: dict = {}              # op -> [(anahtar, göreli_ofset, biçim, own)] (statlearn.load_map)
        self.stats: dict = {}                # anahtar -> (değer, zaman): canlı okunan oyuncu değerleri
        self.packets = 0
        self.packet_time = 0.0
        self.connected = False
        self.on_new_cube = None  # callback(Cube)
        self.calib_until = 0.0
        self.calib_ops: Counter = Counter()
        self.calib_pkts0 = 0
        self.calib_start = 0.0
        self.calib_base_until = 0.0
        self.base_samples: dict[int, list[float]] = {}
        self.name_eids: set[int] = set()      # karakter adının geçtiği paketlerdeki varlıklar (belirsizliği çözer)
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

    # Konum sıçrama filtresi: yanlış kaynaktan gelen tek seferlik sapmalar (başkasının/hedefin konumu) atılır
    JUMP_BASE_M = 40.0         # her zaman izin verilen sıçrama
    JUMP_SPEED_MPS = 40.0      # üstüne eklenen en yüksek makul hız (uçuş/binek)
    JUMP_CONFIRM_M = 25.0      # sıçrama adayı: sonraki güncelleme buna bu kadar yakınsa kabul (ışınlanma)

    def set_pos(self, x, y, z, force: bool = False):
        """force=True: ışınlanma / bölge girişi gibi güvenilir kaynaklar filtreden geçmez."""
        if not valid_coord(x, y, z):
            return
        with self._l:
            now = time.time()
            if not force and self.pos is not None:
                dt = min(10.0, max(0.0, now - self.pos_time))
                limit = (self.JUMP_BASE_M + self.JUMP_SPEED_MPS * dt) * 100.0
                jump = math.hypot(x - self.pos[0], y - self.pos[1])
                if jump > limit:
                    cand = self._jump_cand
                    if cand and math.hypot(x - cand[0], y - cand[1]) <= self.JUMP_CONFIRM_M * 100.0:
                        pass                               # ardışık iki güncelleme aynı yeri gösteriyor: gerçek taşınma
                    else:
                        self._jump_cand = (x, y, now)
                        self.diag["pos_outlier"] += 1
                        return
            self._jump_cand = None
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
            self.pos, self.pos_time = (x, y, z), now

    # --- kimlik kalibrasyonu: kullanıcı zıplarken z'si en çok değişen varlık = karakter ---
    CALIB_SECONDS = 6.0
    MIN_SAMPLES = 2          # bir varlıktan en az bu kadar konum örneği
    MIN_Z_RANGE = 40.0       # cm: zıplama sayılacak en küçük yükseklik değişimi
    AMBIGUOUS = 0.6          # ikinci aday en iyinin bu oranını geçerse belirsiz
    BASELINE_SECONDS = 3.0   # önce kullanıcı DURUR: bu sırada zıplayan/hareket eden varlıklar aday olamaz
    BASELINE_MAX_Z = 20.0    # cm: bekleme fazında bundan fazla yükseklik değişimi = başkası

    def start_calibration(self, secs=None, baseline=None):
        """secs verilmezse varsayılan akış: önce BASELINE_SECONDS bekleme, sonra CALIB_SECONDS zıplama."""
        if secs is None:
            secs, baseline = self.CALIB_SECONDS, (self.BASELINE_SECONDS if baseline is None else baseline)
        elif baseline is None:
            baseline = 0.0
        with self._l:
            now = time.time()
            self.samples, self.base_samples = {}, {}
            self.calib_ops = Counter()
            self.calib_pkts0 = self.packets
            self.calib_start = now
            self.calib_base_until = now + baseline
            self.calib_until = now + baseline + secs

    @property
    def calibrating(self):
        return time.time() < self.calib_until

    @property
    def calib_phase(self):
        """'base' (dur), 'jump' (zıpla) veya None."""
        now = time.time()
        if now >= self.calib_until:
            return None
        return "base" if now < self.calib_base_until else "jump"

    def sample(self, eid: int, z: float, now=None):
        now = time.time() if now is None else now
        with self._l:
            if now < self.calib_until and valid_coord(0, 0, z):
                target = self.base_samples if now < self.calib_base_until else self.samples
                target.setdefault(eid, []).append(z)

    def count_op(self, op: int):
        """Kalibrasyon sırasında gelen her opcode'u sayar (tanı için)."""
        with self._l:
            if time.time() < self.calib_until:
                self.calib_ops[op] += 1

    def finish_calibration(self):
        """(eid|None, mesaj). Tanı bilgisi %APPDATA%/A2Radar/calibration.log dosyasına yazılır."""
        with self._l:
            self.calib_until = 0.0
            samples, ops, base = self.samples, self.calib_ops, self.base_samples
            pkts = self.packets - self.calib_pkts0
            self.samples, self.calib_ops, self.base_samples = {}, Counter(), {}
        everyone = sorted(((max(v) - min(v), len(v), e) for e, v in samples.items() if len(v) >= self.MIN_SAMPLES), reverse=True)
        # Bekleme fazında zaten hareket eden/zıplayan varlıklar kullanıcı değildir (kullanıcı duruyordu)
        busy = {e for e, v in base.items() if len(v) >= 2 and max(v) - min(v) >= self.BASELINE_MAX_Z}
        stats = [s for s in everyone if s[2] not in busy]
        note = ""
        if busy and len(stats) != len(everyone):
            note = f" ({len(everyone) - len(stats)} varlık bekleme sırasında da hareket ettiği için elendi)"
        if not stats and everyone:       # herkes hareketliydi (binek/uçuş gibi): elemeden vazgeç
            stats, note = everyone, ""
        most = max((len(v) for v in samples.values()), default=0)
        ids, eid, msg = None, None, ""
        if not stats:
            msg = (f"Zıplama algılanmadı (varlık: {len(samples)}, en çok örnek: {most}, gelen paket: {pkts}). "
                   "Kalibrasyon sırasında sürekli zıpla, dash yap veya kanat aç.")
        elif stats[0][0] < self.MIN_Z_RANGE:
            msg = (f"Yükseklik değişimi yetersiz ({stats[0][0]:.0f} cm < {self.MIN_Z_RANGE:.0f} cm, {stats[0][1]} örnek). "
                   "Daha yüksek/sık zıpla veya kanat aç.")
        elif len(stats) > 1 and stats[1][0] >= self.MIN_Z_RANGE and stats[1][0] > stats[0][0] * self.AMBIGUOUS:
            close = [s for s in stats if s[0] >= self.MIN_Z_RANGE and s[0] > stats[0][0] * self.AMBIGUOUS]
            named = [s for s in close if s[2] in self.name_eids]
            if len(named) == 1:                              # karakter adı paketlerde yalnızca birinde geçti
                eid = named[0][2]
                msg = f"Karakter bulundu (ID {eid}, karakter adıyla ayrıldı, {named[0][0]:.0f} cm){note}"
            else:
                msg = (f"{len(close)} oyuncu zıpladı, belirsiz{note}. ⚙'dan karakter adını gir ve tekrar dene; "
                       "ya da kalabalıktan uzaklaş.")
        else:
            eid = stats[0][2]
            msg = f"Karakter bulundu (ID {eid}, {stats[0][1]} örnek, {stats[0][0]:.0f} cm){note}"
        self._write_calib_log(eid, msg, samples, ops, pkts, base, busy)
        if eid is not None:
            self.set_player_id(eid, lock=True)
            with self._l:
                self.pos = self._anchor = self.heading = None
        elif self.calib_log_hint:
            msg += f" Ayrıntı: {self.calib_log_hint}"
        return eid, msg

    calib_log_hint = ""

    def _write_calib_log(self, eid, msg, samples, ops, pkts, base=None, busy=()):
        try:
            p = Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "calibration.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
                     f"  gelen paket={pkts}, bağlı={self.connected}, varlık={len(samples)}"]
            for e, v in sorted(samples.items(), key=lambda kv: -len(kv[1]))[:10]:
                lines.append(f"  varlık {e}: {len(v)} örnek, z aralığı {max(v) - min(v):.0f} cm")
            if base:
                lines.append("  bekleme fazı: " + ", ".join(
                    f"{e}:{max(v) - min(v):.0f}cm{'*' if e in busy else ''}" for e, v in sorted(base.items(), key=lambda kv: -len(kv[1]))[:8])
                    + "  (* = elendi)")
            if self.name_eids:
                lines.append(f"  adın geçtiği varlıklar: {sorted(self.name_eids)[:8]}")
            lines.append("  en sık opcode: " + ", ".join(f"{o}x{n}" for o, n in ops.most_common(12)))
            with p.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
            self.calib_log_hint = r"%APPDATA%\A2Radar\calibration.log"
        except OSError:
            self.calib_log_hint = ""

    def reset_zone(self):
        self.entities.clear()
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
