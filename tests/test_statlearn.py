import struct
import tempfile
import time
import unittest
from pathlib import Path

from a2radar import statlearn
from a2radar.protocol import Decoder
from a2radar.state import State

OWN = 10599          # varint: e7 52


def vi(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def hp_pkt(hp, eid=OWN):
    return vi(eid) + bytes(5) + struct.pack("<I", hp) + bytes(3)           # HP: eid+5 sonrası


def noise_pkt(eid=OWN):
    return vi(eid) + bytes(2) + struct.pack("<I", 18008) + bytes(4)        # her zaman 18008 (azami gibi)


class LearnTests(unittest.TestCase):
    def setUp(self):
        self.s = State()
        self.s.player_id = OWN
        self.s.learner = statlearn.StatLearner()
        self.d = Decoder(self.s)
        self.tmp = Path(tempfile.mkdtemp()) / "m.json"

    def window(self, value, pkts):
        self.s.learner.start("hp", value, seconds=30)
        for op, p in pkts:
            self.d.packet(op, p)
        self.s.learner.until = 0           # pencereyi kapat

    def test_two_windows_narrow_to_real_field(self):
        self.window(18008, [(20000, hp_pkt(18008)), (20001, noise_pkt()), (20002, hp_pkt(5, eid=999))])
        first = {c[0] for c, _ in self.s.learner.candidates()}
        self.assertIn(20000, first)
        self.assertIn(20001, first)
        self.assertNotIn(20002, first)                       # kendi paketimiz değil
        self.window(17500, [(20000, hp_pkt(17500)), (20001, noise_pkt())])
        self.s.learner.finish()
        c = self.s.learner.candidates()
        self.assertEqual([x[0][0] for x in c if x[0][2] == "u32"], [20000])
        cand = next(x[0] for x in c if x[0][2] == "u32")
        self.assertEqual(cand[1], 5)

    def test_accept_then_live_read_and_persist(self):
        self.window(18008, [(20000, hp_pkt(18008))])
        self.window(17500, [(20000, hp_pkt(17500))])
        self.s.learner.finish()
        cand = next(x[0] for x in self.s.learner.candidates() if x[0][2] == "u32")
        sm = {}
        orig = statlearn.map_path
        statlearn.map_path = lambda: self.tmp
        try:
            self.s.learner.accept(sm, "hp", cand)
            self.s.statmap = statlearn.load_map()
        finally:
            statlearn.map_path = orig
        self.d.packet(20000, hp_pkt(12345))
        self.assertEqual(self.s.stats["hp"][0], 12345)
        self.d.packet(20000, hp_pkt(1, eid=999))              # başkasının paketi: yok say
        self.assertEqual(self.s.stats["hp"][0], 12345)

    def test_float_and_bad_map(self):
        lr = statlearn.StatLearner()
        lr.start("mp", 4788.0, 30)
        lr.feed(1, vi(OWN) + bytes(2) + struct.pack("<f", 4788.0), OWN)
        self.assertTrue(any(c[0][2] == "f32" for c in lr.candidates()))
        p = Path(tempfile.mkdtemp()) / "x.json"
        p.write_text('{"hp": {"op": "x"}, "mp": 5}', "utf-8")
        self.assertEqual(statlearn.load_map(p), {})
        self.assertEqual(statlearn.load_map(Path("yok.json")), {})


if __name__ == "__main__":
    unittest.main()
