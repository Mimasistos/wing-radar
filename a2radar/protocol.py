from __future__ import annotations

import struct
from collections import defaultdict

try:
    import lz4.block as _lz4
except ImportError:  # testler lz4'süz da çalışsın
    _lz4 = None

from . import opcodes
from .state import Cube, State

MAX_PLAIN = 262_144
MAX_BUFFER = 1 << 20
MAX_PENDING = 64


def uvarint(data, off=0):
    v = 0
    for i in range(5):
        if off + i >= len(data):
            raise ValueError("eksik varint")
        b = data[off + i]
        v |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return v, i + 1
    raise ValueError("varint > 5 bayt")


def f3(buf, off):
    if off < 0 or len(buf) < off + 12:
        return None
    return struct.unpack_from("<fff", buf, off)


class Flow:
    """Tek yönlü TCP akışı: seq numarasına göre sıralar, tekrarları atar."""

    def __init__(self):
        self.next_seq = None
        self.buf = bytearray()
        self.pending: dict[int, bytes] = {}

    def add(self, seq: int, payload: bytes) -> bool:
        """True dönerse buf'a yeni bayt eklendi."""
        if self.next_seq is None:
            self.next_seq = seq
        diff = (seq - self.next_seq) & 0xFFFFFFFF
        if diff >= 0x80000000:  # geçmiş: kısmen/tamamen tekrar
            skip = (self.next_seq - seq) & 0xFFFFFFFF
            if skip >= len(payload):
                return False
            payload = payload[skip:]
        elif diff > 0:
            if len(self.pending) >= MAX_PENDING:  # kayıp paket: yeniden senkronize ol
                self.pending.clear()
                self.buf.clear()
                self.next_seq = seq
            else:
                self.pending[seq] = payload
                return False
        while True:
            self.buf += payload
            self.next_seq = (self.next_seq + len(payload)) & 0xFFFFFFFF
            payload = self.pending.pop(self.next_seq, None)
            if payload is None:
                return True


class Decoder:
    def __init__(self, state: State, port: int | None = None, cfg: dict | None = None):
        self.s = state
        self.port = port
        self.cfg = cfg or opcodes.load()
        self.flows: dict[tuple, Flow] = defaultdict(Flow)
        c = self.cfg
        self.fixed = {o: e["off"] for e in c["self_pos_fixed"] for o in e["op"]}
        self.tail = {o: e.get("min_len", 30) for e in c["self_pos_tail"] for o in e["op"]}
        self.rel = {o: e["off"] for e in c["self_pos_n"] for o in e["op"]}
        self.spawn = {e["op"]: e for e in c["spawn"]}
        self.despawn = set(c["despawn"])
        self.autobind = set(c["autobind"])

    # --- TCP katmanı ---
    def feed(self, src, sport, dst, dport, seq, payload: bytes):
        if not payload or (self.port is not None and sport != self.port):
            return
        flow = self.flows[(src, sport, dst, dport)]
        if not flow.add(seq, payload):
            return
        self.s.touch_packet()
        self._drain(flow.buf)
        if len(flow.buf) > MAX_BUFFER:
            flow.buf.clear()

    def _drain(self, buf: bytearray):
        off = 0
        while off < len(buf):
            if buf[off] == 0:
                off += 1
                continue
            try:
                enc, vl = uvarint(buf, off)
            except ValueError:
                if len(buf) - off >= 5:  # gerçekten bozuk: 1 bayt atla
                    off += 1
                    continue
                break
            if enc < 4 or enc - 4 > MAX_PLAIN:
                off += 1
                continue
            end = off + vl + enc - 4
            if end > len(buf):
                break
            self.frame(bytes(buf[off + vl:end]), 0)
            off = end
        del buf[:off]

    # --- çerçeve katmanı ---
    def frame(self, body: bytes, depth: int):
        if len(body) < 2 or depth > 4:
            return
        hdr = None
        if body[:2] == b"\xff\xff" and len(body) >= 6:
            hdr = 2
        elif len(body) >= 7 and 240 <= body[0] < 255 and body[1:3] == b"\xff\xff":
            hdr = 3
        if hdr is not None and _lz4 is not None:
            size = struct.unpack_from("<I", body, hdr)[0]
            if 0 < size <= MAX_PLAIN:
                try:
                    self._walk(_lz4.decompress(body[hdr + 4:], uncompressed_size=size), depth + 1)
                    return
                except Exception:
                    pass
        try:
            self.packet(struct.unpack_from("<H", body)[0], body[2:])
        except (struct.error, ValueError, IndexError):
            pass

    def _walk(self, data: bytes, depth: int):
        off = 0
        while off < len(data):
            if data[off] == 0:
                off += 1
                continue
            try:
                enc, vl = uvarint(data, off)
            except ValueError:
                return
            blen = enc - 4
            if blen < 2:
                off += vl + max(0, blen)
                continue
            if off + vl + blen > len(data):
                return
            self.frame(data[off + vl:off + vl + blen], depth)
            off += vl + blen

    # --- opcode katmanı ---
    def _mine(self, eid):
        return self.s.player_id is not None and eid == self.s.player_id

    def packet(self, op: int, p: bytes):
        s, c = self.s, self.cfg
        if s.char_name:
            self._name_bind(p)
        if op in self.fixed:
            eid, _ = uvarint(p)
            if self._mine(eid) and (v := f3(p, self.fixed[op])):
                s.set_pos(*v)
        elif op in self.tail:
            eid, _ = uvarint(p)
            if s.calibrating and eid > 100 and (v := f3(p, len(p) - 12)):
                s.sample(eid, v[2])
            if op in self.autobind and not s.player_id and eid > 100:
                s.set_player_id(eid, lock=False)
            if self._mine(eid) and len(p) >= self.tail[op] and (v := f3(p, len(p) - 12)):
                s.set_pos(*v)
        elif op in self.rel:
            eid, n = uvarint(p)
            if self._mine(eid) and (v := f3(p, n + self.rel[op])):
                s.set_pos(*v)
        elif op == c["self_info"]["op"]:
            eid, _ = uvarint(p)
            s.set_player_id(eid, lock=True)
            if v := f3(p, c["self_info"]["off"]):
                s.set_pos(*v)
        elif op == c["zone_enter"]["op"]:
            s.reset_zone()
            eid, n = uvarint(p)
            s.set_player_id(eid, lock=True)
            if v := f3(p, n + c["zone_enter"]["off"]):
                s.set_pos(*v)
        elif op == c["teleport"]["op"]:
            if v := f3(p, c["teleport"]["off"]):
                s.set_pos(*v)
        elif op in self.spawn:
            e = self.spawn[op]
            eid, n = uvarint(p)
            if len(p) < n + e["pos_off"] + 12 or len(p) < n + e["id_off"] + 4:
                return
            npc = struct.unpack_from("<I", p, n + e["id_off"])[0]
            if opcodes.is_cube(npc):
                x, y, z = f3(p, n + e["pos_off"])
                s.add_cube(Cube(eid, npc, x, y, z, opcodes.is_asmodian(npc)))
        elif op in self.despawn:
            s.remove_cube(uvarint(p)[0])
        elif op == c["cube_pickup_nearby"]:
            s.remove_nearby(600.0)

    def _name_bind(self, p: bytes):
        name = self.s.char_name.strip()
        if not name:
            return
        low = p.lower()
        for m in (name.encode(), name.encode("utf-16le"),
                  name.lower().encode(), name.lower().encode("utf-16le")):
            if m in p or m in low:
                try:
                    eid, _ = uvarint(p)
                except ValueError:
                    return
                if eid > 100 and eid != self.s.player_id:
                    self.s.set_player_id(eid, lock=True)
                return
