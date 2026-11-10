"""Kendi kimliğimi paket akışından doğrulama ve konum sıçrama filtresi (başka oyuncular konumu bozmasın)."""
import struct
import unittest

from a2radar.protocol import Decoder
from a2radar.state import State

OWN, OTHER = 10599, 33751
TARGET_HIT_14338 = ("e75200408e030164028e9301f4374f42d5650cc86384d3c30052c8469e550100")   # konum = hedefin (Brax)


def vi(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def self_pkt(eid):
    return vi(eid) + b"\x01\x01\x06\x5a\x3e\x08\x00"


class SelfIdTests(unittest.TestCase):
    def setUp(self):
        self.s = State()
        self.d = Decoder(self.s)

    def feed(self, eid, n, op=36096):
        for _ in range(n):
            self.d.packet(op, self_pkt(eid))

    def test_wrong_binding_is_corrected(self):
        self.s.set_player_id(OTHER, lock=True)
        self.feed(OWN, 3)
        self.assertEqual(self.s.player_id, OTHER)            # henüz yeterli oy yok
        self.feed(OWN, 3)
        self.assertEqual(self.s.player_id, OWN)
        self.assertEqual(self.s.diag["selfid_rebind"], 1)

    def test_unbound_state_binds_from_votes(self):
        self.feed(OWN, 5, op=14342)
        self.assertEqual(self.s.player_id, OWN)

    def test_correct_binding_is_kept(self):
        self.s.set_player_id(OWN, lock=True)
        self.feed(OWN, 6)
        self.feed(OTHER, 4)                                  # başkası da görünse bağlı kimlik oy aldığı için değişmez
        self.assertEqual(self.s.player_id, OWN)
        self.assertEqual(self.s.diag["selfid_rebind"], 0)

    def test_no_dominant_eid_no_rebind(self):
        self.s.set_player_id(OTHER, lock=True)
        for e in (101, 102, 103, 104, 105, 106):
            self.feed(e, 1)
        self.assertEqual(self.s.player_id, OTHER)

    def test_not_while_calibrating(self):
        self.s.set_player_id(OTHER, lock=True)
        self.s.start_calibration(60)
        self.feed(OWN, 8)
        self.assertEqual(self.s.player_id, OTHER)

    def test_attack_packet_target_position_is_not_own_position(self):
        self.s.set_player_id(OWN, lock=True)
        self.d.packet(14338, bytes.fromhex(TARGET_HIT_14338))
        self.assertIsNone(self.s.pos)                        # eskiden konum canavarın yerine atlıyordu


class JumpFilterTests(unittest.TestCase):
    def test_outlier_rejected_then_normal_accepted(self):
        s = State()
        s.set_pos(-143700.0, -600.0, 25600.0)
        s.set_pos(-143700.0 + 50000, -600.0, 25600.0)         # 500 m ötede tek seferlik sapma
        self.assertAlmostEqual(s.pos[0], -143700.0)
        self.assertEqual(s.diag["pos_outlier"], 1)
        s.set_pos(-143600.0, -600.0, 25600.0)                 # normal güncelleme geçer
        self.assertAlmostEqual(s.pos[0], -143600.0)

    def test_two_consistent_far_updates_are_a_real_move(self):
        s = State()
        s.set_pos(-143700.0, -600.0, 25600.0)
        s.set_pos(-80000.0, -600.0, 25600.0)
        self.assertAlmostEqual(s.pos[0], -143700.0)           # ilk: aday
        s.set_pos(-79900.0, -650.0, 25600.0)
        self.assertAlmostEqual(s.pos[0], -79900.0)            # ikinci: kabul (ışınlanma / uzak taşınma)

    def test_alternating_sources_never_accept_spike(self):
        s = State()
        s.set_pos(-143700.0, -600.0, 25600.0)
        for i in range(10):                                   # gerçek konum ile sapma sırayla gelirse sapma geçmez
            s.set_pos(-143700.0 + i * 50, -600.0, 25600.0)
            s.set_pos(-143700.0 + 60000, 90000.0, 25600.0)
        self.assertLess(abs(s.pos[0] + 143700.0), 2000)

    def test_force_bypasses_filter(self):
        s = State()
        s.set_pos(-143700.0, -600.0, 25600.0)
        s.set_pos(-50000.0, 1000.0, 25600.0, force=True)      # ışınlanma / bölge girişi
        self.assertAlmostEqual(s.pos[0], -50000.0)


if __name__ == "__main__":
    unittest.main()
