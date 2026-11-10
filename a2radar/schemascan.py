"""Şema taraması: oyuncunun yakınında 'konum gibi görünen' float üçlüleri hangi opcode/ofsette çıkıyor?

Süre boyunca gelen her paketin her bayt ofsetini (<fff) olarak okur; oyuncunun konumuna yakın
(yatay RADIUS_CM, dikey Z_CM) olanları opcode+ofset bazında sayar. Gerçek konum alanları
paket başına tutarlı isabet alır, rastgele baytlar almaz. Sonuç %APPDATA%/A2Radar/schema_scan.json.
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
from collections import defaultdict
from pathlib import Path

RADIUS_CM = 40_000.0     # 400 m
Z_CM = 15_000.0          # 150 m
MAX_OFF = 200
MAX_SAMPLES = 6


def out_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "schema_scan.json"


def _uvarint(p):
    v = 0
    for i in range(min(5, len(p))):
        v |= (p[i] & 0x7F) << (7 * i)
        if not p[i] & 0x80:
            return v, i + 1
    return None, 0


class SchemaScan:
    def __init__(self, seconds: float = 40.0):
        self.until = time.time() + seconds
        self.lock = threading.Lock()
        self.total = defaultdict(int)                    # op -> paket sayısı
        self.abs_hits = defaultdict(lambda: defaultdict(int))
        self.rel_hits = defaultdict(lambda: defaultdict(int))
        self.samples = defaultdict(list)                 # op -> [(hex, [ofsetler])]
        self.eids = defaultdict(set)                     # op -> farklı varlık sayısı için
        self.packets = 0
        self.hist = defaultdict(int)                     # op -> toplam paket (konumdan bağımsız)
        self.first = {}                                  # op -> ilk paketin ilk 64 baytı

    @property
    def active(self) -> bool:
        return time.time() < self.until

    def feed(self, op: int, p: bytes, pos):
        with self.lock:
            self.hist[op] += 1
            if op not in self.first and len(self.first) < 400:
                self.first[op] = p[:64].hex()
        if pos is None or len(p) < 12:
            return
        px, py, pz = pos
        if abs(px) + abs(py) < 1000:   # sıfıra yakın geçersiz konum her ofseti isabet yapar
            return
        eid, n = _uvarint(p)
        hits = []
        unpack = struct.unpack_from
        for off in range(0, min(len(p) - 11, MAX_OFF)):
            x, y, z = unpack("<fff", p, off)
            if abs(x - px) < RADIUS_CM and abs(y - py) < RADIUS_CM and abs(z - pz) < Z_CM and (x or y or z):
                hits.append(off)
        with self.lock:
            self.packets += 1
            self.total[op] += 1
            if eid is not None:
                self.eids[op].add(eid)
            if hits:
                for off in hits:
                    self.abs_hits[op][off] += 1
                    self.rel_hits[op][off - n] += 1
                if len(self.samples[op]) < MAX_SAMPLES:
                    self.samples[op].append({"hex": p[:96].hex(), "n": n, "hits": hits})

    def report(self) -> dict:
        with self.lock:
            rows = []
            for op, hits in self.abs_hits.items():
                tot = self.total[op]
                best_abs = sorted(hits.items(), key=lambda kv: -kv[1])[:4]
                best_rel = sorted(self.rel_hits[op].items(), key=lambda kv: -kv[1])[:4]
                top = best_abs[0][1]
                rows.append({
                    "op": op, "packets": tot, "entities": len(self.eids[op]),
                    "best_abs_off": [{"off": o, "hits": c, "ratio": round(c / tot, 2)} for o, c in best_abs],
                    "best_rel_off(n+)": [{"off": o, "hits": c, "ratio": round(c / tot, 2)} for o, c in best_rel],
                    "_score": top,
                    "samples": self.samples[op],
                })
            rows.sort(key=lambda r: -r["_score"])
            for r in rows:
                r.pop("_score")
            hist = [{"op": op, "packets": c, "first_hex": self.first.get(op, "")}
                    for op, c in sorted(self.hist.items(), key=lambda kv: -kv[1])[:60]]
            return {"packets_scanned": self.packets, "ops_with_hits": len(rows), "ops": rows[:25],
                    "op_histogram": hist}

    def write(self) -> Path:
        path = out_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=1), "utf-8")
        return path
