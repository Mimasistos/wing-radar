"""Değer öğrenme: ekranda gördüğün bir sayının (can, mana, savaş gücü...) hangi pakette olduğunu bulur.

Nasıl kullanılır: ekrandaki değeri gir, 'Ara' de; kısa bir pencere boyunca gelen paketlerde o değer (u16/u32/float)
aranır. Değer değişince (hasar al, mana harca) yeni değeri gir ve tekrar ara: iki pencerede de eşleşen
(opcode, ofset, biçim) adayları kalır. Tek aday kalınca 'Kabul et' ile haritalanır ve canlı okunur.
Ofsetler baştaki varlık kimliği varintine göre göreli tutulur (kimlik 2 veya 3 bayt olabilir).
Harita: %APPDATA%/A2Radar/statmap.json  {anahtar: {op, off, fmt, own}}.
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
from pathlib import Path

FMTS = (("u16", "<H", 2), ("u32", "<I", 4), ("f32", "<f", 4))
MAX_OFF = 160
STATS = (("hp", "Can"), ("hp_max", "Can (azami)"), ("mp", "Mana"), ("mp_max", "Mana (azami)"),
         ("energy", "Enerji"), ("cp", "Savaş gücü"), ("gs", "Gear score"), ("level", "Seviye"))


def map_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "statmap.json"


def _uvarint(p):
    v = 0
    for i in range(min(5, len(p))):
        v |= (p[i] & 0x7F) << (7 * i)
        if not p[i] & 0x80:
            return v, i + 1
    return None, 0


def load_map(path=None) -> dict:
    """{op: [(anahtar, göreli_ofset, biçim, own)]}"""
    try:
        raw = json.loads(Path(path or map_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[int, list] = {}
    for key, s in raw.items() if isinstance(raw, dict) else ():
        try:
            if s["fmt"] in {f[0] for f in FMTS}:
                out.setdefault(int(s["op"]), []).append((key, int(s["off"]), s["fmt"], bool(s.get("own", True))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_map(statmap: dict, path=None):
    flat = {k: {"op": op, "off": off, "fmt": fmt, "own": own}
            for op, lst in statmap.items() for (k, off, fmt, own) in lst}
    p = Path(path or map_path())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(flat, indent=1), "utf-8")
        tmp.replace(p)
    except OSError:
        pass


def read_value(p: bytes, off_rel: int, fmt: str):
    eid, n = _uvarint(p)
    spec = {f[0]: f for f in FMTS}.get(fmt)
    if eid is None or spec is None:
        return None
    off = n + off_rel
    if off < 0 or off + spec[2] > len(p):
        return None
    return struct.unpack_from(spec[1], p, off)[0]


class StatLearner:
    def __init__(self):
        self._l = threading.Lock()
        self.key = None
        self.until = 0.0
        self.target = None
        self.win: dict = {}              # bu pencerede eşleşen aday -> sayı
        self.cands: dict | None = None   # önceki pencerelerle kesişim (aday -> en az sayı)
        self.windows = 0

    # --- pencere ---
    def start(self, key: str, value: float, seconds: float = 8.0):
        with self._l:
            self._merge()
            if key != self.key:
                self.key, self.cands, self.windows = key, None, 0
            self.target, self.win, self.until = float(value), {}, time.time() + seconds

    @property
    def active(self) -> bool:
        return time.time() < self.until

    def _merge(self):
        if self.target is None:
            return
        if self.cands is None:
            self.cands = dict(self.win)
        else:
            self.cands = {c: min(n, self.win[c]) for c, n in self.cands.items() if c in self.win}
        self.windows += 1
        self.target, self.win = None, {}

    def reset(self):
        with self._l:
            self.key, self.cands, self.windows, self.target, self.win, self.until = None, None, 0, None, {}, 0.0

    def feed(self, op: int, p: bytes, own_eid: int | None):
        if not self.active or len(p) < 3:
            return
        t = self.target
        eid, n = _uvarint(p)
        if eid is None:
            return
        own = own_eid is not None and eid == own_eid
        hits = []
        for off in range(n, min(len(p), MAX_OFF)):
            for name, fmt, size in FMTS:
                if off + size > len(p):
                    continue
                v = struct.unpack_from(fmt, p, off)[0]
                ok = abs(v - t) <= max(0.01, abs(t) * 1e-6) if name == "f32" else v == t
                if ok:
                    hits.append((op, off - n, name, own))
        if hits:
            with self._l:
                for h in hits:
                    self.win[h] = self.win.get(h, 0) + 1

    # --- sonuç ---
    def candidates(self, own_only: bool = True) -> list[tuple]:
        """[(aday, sayı)] çok görülen önce. Pencere sürerken önceki kesişim + şimdiki pencere önizlemesi."""
        with self._l:
            base = self.cands
            if self.target is not None:
                cur = self.win
                base = dict(cur) if base is None else {c: min(n, cur[c]) for c, n in base.items() if c in cur}
            if base is None:
                return []
            items = [(c, n) for c, n in base.items() if (c[3] or not own_only)]
        items.sort(key=lambda x: (-x[1], x[0][0], x[0][1]))
        return items

    def finish(self):
        with self._l:
            self._merge()

    def accept(self, statmap: dict, key: str, cand: tuple):
        op, off, fmt, own = cand
        lst = [e for e in statmap.get(op, []) if e[0] != key]
        lst.append((key, off, fmt, own))
        statmap[op] = lst
        for o in list(statmap):
            if o != op:
                statmap[o] = [e for e in statmap[o] if e[0] != key]
                if not statmap[o]:
                    del statmap[o]
        save_map(statmap)
