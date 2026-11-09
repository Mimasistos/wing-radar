import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2radar import opcodes
from a2radar.protocol import Decoder, Flow, uvarint
from a2radar.state import State, nearest


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def frame(op, payload):
    body = struct.pack("<H", op) + payload
    return varint(len(body) + 4) + body


def f3(x, y, z):
    return struct.pack("<fff", x, y, z)


CUBE = 1000550
PLAYER = 5000


def spawn(eid, npc, x, y, z):
    # n = len(varint(eid)); id at n+2, pos at n+6
    return frame(13876, varint(eid) + b"\0\0" + struct.pack("<I", npc) + f3(x, y, z))


def zone_enter(eid, x, y, z):
    return frame(13859, varint(eid) + b"\0" * 5 + f3(x, y, z))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.s = State()
        self.d = Decoder(self.s, port=13328)

    def feed(self, data, seq=1000):
        self.d.feed("1.1.1.1", 13328, "2.2.2.2", 50000, seq, data)

    def test_uvarint(self):
        self.assertEqual(uvarint(varint(300)), (300, 2))

    def test_zone_enter_binds_and_positions(self):
        self.feed(zone_enter(PLAYER, 100, 200, 300))
        self.assertEqual(self.s.player_id, PLAYER)
        self.assertEqual(self.s.pos, (100, 200, 300))

    def test_cube_spawn_and_despawn(self):
        first = zone_enter(PLAYER, 0, 0, 0) + spawn(77, CUBE, 3000, 4000, 500)
        self.feed(first)
        hit = nearest(self.s.snapshot(), 150)
        self.assertAlmostEqual(hit[1], (30 ** 2 + 40 ** 2 + 5 ** 2) ** .5, places=3)
        self.feed(frame(13877, varint(77)), seq=1000 + len(first))
        self.assertEqual(self.s.snapshot().cubes, [])

    def test_non_cube_ignored(self):
        self.feed(spawn(78, 999, 0, 0, 0))
        self.assertEqual(self.s.snapshot().cubes, [])

    def test_fragmented_stream(self):
        data = zone_enter(PLAYER, 1, 2, 3) + spawn(9, CUBE, 10, 10, 10)
        a, b = data[:7], data[7:]
        self.feed(a, 1000)
        self.assertIsNone(self.s.pos)
        self.feed(b, 1000 + len(a))
        self.assertEqual(len(self.s.snapshot().cubes), 1)

    def test_out_of_order_and_retransmit(self):
        data = zone_enter(PLAYER, 1, 2, 3) + spawn(9, CUBE, 10, 10, 10)
        a, b = data[:20], data[20:]
        self.feed(bytes(1), 999)      # stream start
        self.feed(b, 1020)          # sonra gelmesi gereken parça önce
        self.feed(a, 1000)
        self.feed(a, 1000)          # tekrar gönderim
        self.assertEqual(len(self.s.snapshot().cubes), 1)

    def test_garbage_does_not_crash(self):
        junk = b"" * 20
        self.feed(junk)
        self.feed(zone_enter(PLAYER, 5, 6, 7), seq=1000 + len(junk))
        self.assertEqual(self.s.player_id, PLAYER)

    def test_invalid_coords_rejected(self):
        self.feed(zone_enter(PLAYER, float("nan"), 0, 0))
        self.assertIsNone(self.s.pos)

    def test_heading_from_movement(self):
        self.s.set_pos(0, 0, 0)
        self.s.set_pos(0, -500, 0)   # kuzeye (−y)
        self.assertAlmostEqual(self.s.heading, 0.0)

    def test_wrong_entity_move_ignored(self):
        self.feed(zone_enter(PLAYER, 0, 0, 0))
        p = varint(PLAYER + 1) + b"\0" * 40 + f3(9, 9, 9)
        z = zone_enter(PLAYER, 0, 0, 0)
        self.feed(frame(14378, p), seq=1000 + len(z))
        self.assertEqual(self.s.pos, (0, 0, 0))

    def test_opcode_helpers(self):
        self.assertTrue(opcodes.is_cube(1000295))
        self.assertFalse(opcodes.is_cube(1))


class CalibrationTests(unittest.TestCase):
    def _feed_moves(self, d, eid, zs, seq):
        for z in zs:
            body = varint(eid) + bytes(40) + f3(0, 0, z)
            fr = frame(14378, body)
            d.feed("1.1.1.1", 13328, "2.2.2.2", 5, seq, fr)
            seq += len(fr)
        return seq

    def test_picks_jumping_entity(self):
        s = State()
        d = Decoder(s, 13328)
        s.start_calibration(60)
        q = self._feed_moves(d, 111, [0, 0, 0, 0], 1)           # duran oyuncu
        q = self._feed_moves(d, 222, [0, 120, 200, 80, 0], q)   # zıplayan: karakter
        eid, _ = s.finish_calibration()
        self.assertEqual(eid, 222)
        self.assertEqual(s.player_id, 222)

    def test_ambiguous_rejected(self):
        s = State()
        d = Decoder(s, 13328)
        s.start_calibration(60)
        q = self._feed_moves(d, 111, [0, 150, 0], 1)
        self._feed_moves(d, 222, [0, 160, 0], q)
        self.assertIsNone(s.finish_calibration()[0])

    def test_no_autobind(self):
        s = State()
        d = Decoder(s, 13328)
        self._feed_moves(d, 333, [0, 0, 0], 1)
        self.assertIsNone(s.player_id)


class FlowTests(unittest.TestCase):
    def test_seq_wraparound(self):
        f = Flow()
        f.add(0xFFFFFFFE, b"ab")
        f.add(0, b"cd")
        self.assertEqual(bytes(f.buf), b"abcd")


if __name__ == "__main__":
    unittest.main()
