from __future__ import annotations

import struct
import time
import traceback
from collections import Counter, defaultdict, deque

try:
    import lz4.block as _lz4
except ImportError:  # testler lz4'süz da çalışsın
    _lz4 = None

from . import opcodes
from .state import Cube, State, valid_coord

MAX_PLAIN = 262_144
MAX_BUFFER = 1 << 20
MAX_PENDING = 64
GAP_S = 1.0          # eksik TCP parçası için bekleme
STALL_S = 1.5        # tamamlanmayan çerçeve için bekleme (sonra hizayı yitirmiş sayılır)
CHAIN = 2            # resync modunda doğrulanan ardışık çerçeve başlığı


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
    """Tek yönlü TCP akışı: seq numarasına göre sıralar, tekrarları atar.

    Sniffer bir parçayı kaçırırsa (yoğun yükte olur) eksik parça hiç gelmez. GAP_S sonra boşluk atlanır,
    çerçeve sınırı yitirilmiş sayılır (resync) ve Decoder hizayı yeniden bulur.
    """

    def __init__(self):
        self.next_seq = None
        self.buf = bytearray()
        self.pending: dict[int, bytes] = {}
        self.gap_since = None
        self.resync = False          # çerçeve hizası belirsiz: Decoder sıkı doğrulamayla ilerler
        self.good = 0                # resync modunda arka arkaya kabul edilen çerçeve
        self.stall_since = None
        self.gaps = 0

    def add(self, seq: int, payload: bytes, now: float | None = None) -> bool:
        """True dönerse buf'a yeni bayt eklendi."""
        now = time.time() if now is None else now
        if self.next_seq is None:
            self.next_seq = seq
        diff = (seq - self.next_seq) & 0xFFFFFFFF
        if diff >= 0x80000000:  # geçmiş: kısmen/tamamen tekrar
            skip = (self.next_seq - seq) & 0xFFFFFFFF
            if skip >= len(payload):
                return False
            payload = payload[skip:]
        elif diff > 0:
            if not self.pending:
                self.gap_since = now
            self.pending[seq] = payload
            if len(self.pending) >= MAX_PENDING or now - self.gap_since > GAP_S:
                return self._skip_gap(now)      # kayıp parça: beklemeyi bırak
            return False
        return self._append(payload, now)

    def _append(self, payload: bytes, now: float) -> bool:
        while True:
            self.buf += payload
            self.next_seq = (self.next_seq + len(payload)) & 0xFFFFFFFF
            payload = self.pending.pop(self.next_seq, None)
            if payload is None:
                self.gap_since = (self.gap_since or now) if self.pending else None
                return True

    def _skip_gap(self, now: float) -> bool:
        base = self.next_seq
        first = min(self.pending, key=lambda q: (q - base) & 0xFFFFFFFF)
        payload = self.pending.pop(first)
        self.buf.clear()                         # boşluğa denk gelen çerçeve yitti; baştaki parça artık çöp
        self.next_seq = first
        self.resync, self.good, self.stall_since, self.gap_since = True, 0, None, None
        self.gaps += 1
        return self._append(payload, now)


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
        self.known_ops: set[int] = set()
        self.self_ops = set(c.get("self_ops", ()))
        self.self_votes: deque = deque()          # (zaman, eid): kendi karakterime gelen paketlerin ilk kimliği
        self._learn = True

    # --- TCP katmanı ---
    def feed(self, src, sport, dst, dport, seq, payload: bytes, now: float | None = None):
        if not payload or (self.port is not None and sport != self.port):
            return
        now = time.time() if now is None else now
        flow = self.flows[(src, sport, dst, dport)]
        g0 = flow.gaps
        ok = flow.add(seq, payload, now)
        if flow.gaps != g0:
            self.s.diag["gap"] += flow.gaps - g0
        if not ok:
            return
        self.s.touch_packet()
        self._drain(flow, now)
        if len(flow.buf) > MAX_BUFFER:
            flow.buf.clear()
            flow.resync = True

    def _body_ok(self, buf, pos) -> bool:
        """Çerçeve gövdesi inandırıcı mı: lz4 işareti ya da daha önce çözülmüş bir opcode."""
        if pos + 2 > len(buf):
            return True
        b0, b1 = buf[pos], buf[pos + 1]
        if b0 == 0xFF and b1 == 0xFF:
            return True
        if 240 <= b0 < 255 and pos + 3 <= len(buf) and buf[pos + 1:pos + 3] == b"\xff\xff":
            return True
        return not self.known_ops or (b0 | b1 << 8) in self.known_ops

    def _plausible(self, buf, off) -> bool:
        """resync modunda: off'tan başlayan CHAIN çerçevenin başlıkları tutarlı mı."""
        for _ in range(CHAIN):
            while off < len(buf) and buf[off] == 0:
                off += 1
            if off >= len(buf):
                return True
            try:
                enc, vl = uvarint(buf, off)
            except ValueError:
                return len(buf) - off < 5
            if enc < 4 or enc - 4 > MAX_PLAIN or not self._body_ok(buf, off + vl):
                return False
            end = off + vl + enc - 4
            if end >= len(buf):
                return True
            off = end
        return True

    def _drain(self, flow: Flow, now: float | None = None):
        buf = flow.buf
        now = time.time() if now is None else now
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
            if flow.resync and not self._plausible(buf, off):
                off += 1                 # hiza belirsiz: gerçek çerçeve sınırını bayt bayt ara
                continue
            end = off + vl + enc - 4
            if end > len(buf):
                # eksik çerçeve: makul süre bekle; sonra hizayı yitirmiş say (sahte uzunluk yüzlerce KB bekletirdi)
                if flow.stall_since is None:
                    flow.stall_since = now
                elif now - flow.stall_since > STALL_S:
                    flow.stall_since, flow.resync, flow.good = None, True, 0
                    self.s.diag["stall"] += 1
                    off += 1
                    continue
                break
            self._learn = not flow.resync
            self.frame(bytes(buf[off + vl:end]), 0)
            off = end
            flow.stall_since = None
            if flow.resync:
                flow.good += 1
                if flow.good >= 3:
                    flow.resync, flow.good = False, 0
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
            op = struct.unpack_from("<H", body)[0]
            if self._learn:
                self.known_ops.add(op)
            self.packet(op, body[2:])
        except (struct.error, ValueError, IndexError):
            pass
        except Exception as exc:        # beklenmedik hata: tek paketi atla, akışı kilitleme; tanı için kaydet
            self.s.diag["decode_error"] += 1
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            self.s.last_decode_error = f"{type(exc).__name__}: {exc} ({tb.name}:{tb.lineno})"

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
        sc = s.scan
        if sc is not None and sc.active:
            sc.feed(op, p, s.pos)
        lr = s.learner
        if lr is not None and lr.active:
            lr.feed(op, p, s.player_id)
        sm = s.statmap.get(op)
        if sm:
            self._stats(sm, p)
        if op in self.self_ops:
            self._self_vote(p)
        if s.char_name:
            self._name_bind(p)
        if s.calibrating:
            s.count_op(op)
        if op in self.fixed:
            eid, _ = uvarint(p)
            v = f3(p, self.fixed[op])
            # 14338/14378 gibi paketlerde konum başka bir varlığa (hedef/oyuncu) ait olabilir:
            # varlık takibine YAZMA (yalnızca kendi konumu ve kalibrasyon için kullanılır)
            if s.calibrating and eid > 100 and v:
                s.sample(eid, v[2])
            if self._mine(eid) and v:
                s.set_pos(*v)
        elif op in self.tail:
            eid, _ = uvarint(p)
            v = f3(p, len(p) - 12)
            if s.calibrating and eid > 100 and v:
                s.sample(eid, v[2])
            if op in self.autobind and not s.player_id and eid > 100:
                s.set_player_id(eid, lock=False)
            if self._mine(eid) and len(p) >= self.tail[op] and v:
                s.set_pos(*v)
        elif op in self.rel:
            eid, n = uvarint(p)
            v = f3(p, n + self.rel[op])
            if v and valid_coord(*v):
                s.entities.move(eid, *v)
            if s.calibrating and eid > 100 and v:
                s.sample(eid, v[2])
            if self._mine(eid) and v:
                s.set_pos(*v)
        elif op == c["self_info"]["op"]:
            eid, _ = uvarint(p)
            s.set_player_id(eid, lock=True)
            if v := f3(p, c["self_info"]["off"]):
                s.set_pos(*v, force=True)
        elif op == c["zone_enter"]["op"]:
            s.reset_zone()
            eid, n = uvarint(p)
            s.set_player_id(eid, lock=True)
            if v := f3(p, n + c["zone_enter"]["off"]):
                s.set_pos(*v, force=True)
        elif op == c["teleport"]["op"]:
            if v := f3(p, c["teleport"]["off"]):
                s.set_pos(*v, force=True)
        elif op in self.spawn:
            e = self.spawn[op]
            eid, n = uvarint(p)
            pos_off, id_off = e["pos_off"], e["id_off"]
            player = False
            if op == 13889 and len(p) > n + 4:
                # oyuncu biçimi: [tür=0x1f][00][01][ad uzunluğu][ad…][6 bayt][konum]
                ln = p[n + 3]
                nm = p[n + 4:n + 4 + ln]
                if p[n] == 0x1F and 1 <= ln <= 24 and len(nm) == ln and nm.decode("utf-8", "ignore").isprintable():
                    player, pos_off = True, 10 + ln
                    pname = nm.decode("utf-8", "ignore")
                    s.player_names[eid] = pname
                    want = s.char_name.strip().lower()
                    if want and pname.lower() == want and not s.calibrating and eid != s.player_id:
                        s.set_player_id(eid, lock=True)      # adı bilinen oyuncu spawn'ı: güvenilir eşleşme
            if len(p) < n + pos_off + 12 or len(p) < n + id_off + 4:
                return
            npc = -1 if player else struct.unpack_from("<I", p, n + id_off)[0]
            x, y, z = f3(p, n + pos_off)
            if valid_coord(x, y, z):
                s.entities.spawn(eid, npc, x, y, z, op, p)
            if opcodes.is_cube(npc):
                s.add_cube(Cube(eid, npc, x, y, z, opcodes.is_asmodian(npc)))
        elif op in self.despawn:
            eid = uvarint(p)[0]
            s.entities.despawn(eid, op)
            s.remove_cube(eid)
        elif op == c["cube_pickup_nearby"]:
            s.remove_nearby(600.0)

    SELF_WINDOW_S = 90.0
    SELF_MIN_VOTES = 4
    SELF_SHARE = 0.6

    def _self_vote(self, p: bytes):
        """Yalnızca kendi karakterime gelen paketlerin (self_ops) ilk kimliği oy olarak sayılır.

        Bağlı kimlik bu paketlerde hiç görünmüyor, başka bir kimlik ise baskınsa bağlama yanlıştır
        (kalibrasyon başka oyuncuyu seçmiş olabilir): doğru kimliğe geç.
        """
        s = self.s
        try:
            eid, _ = uvarint(p)
        except ValueError:
            return
        if eid <= 100:
            return
        now = time.time()
        v = self.self_votes
        v.append((now, eid))
        while v and now - v[0][0] > self.SELF_WINDOW_S:
            v.popleft()
        if s.calibrating or len(v) < self.SELF_MIN_VOTES:
            return
        cnt = Counter(e for _, e in v)
        leader, n = cnt.most_common(1)[0]
        if leader == s.player_id or n < self.SELF_MIN_VOTES or n / len(v) < self.SELF_SHARE:
            return
        if s.player_id is None or cnt.get(s.player_id, 0) == 0:
            s.set_player_id(leader, lock=True)
            s.diag["selfid_rebind"] += 1

    def _stats(self, spec, p: bytes):
        """statmap'te öğrenilmiş alanları okur (kendi paketimiz değilse own=True olanlar atlanır)."""
        from . import statlearn
        try:
            eid, _ = uvarint(p)
        except ValueError:
            return
        for key, off, fmt, own in spec:
            if own and not self._mine(eid):
                continue
            v = statlearn.read_value(p, off, fmt)
            if v is not None:
                self.s.stats[key] = (v, time.time())

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
                if eid > 100:
                    # yalnızca kalibrasyon eşitlik bozucu: ilk kimlik adın sahibi olmayabilir (ör. başkasına
                    # ait paketin içinde adın geçmesi); kimlik bağlama oyuncu spawn paketinden yapılır
                    self.s.name_eids.add(eid)
                return
