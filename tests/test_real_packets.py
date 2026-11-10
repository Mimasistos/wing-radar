"""Canlı oyundan alınan gerçek paketlerle şema doğrulaması (2026-11-10 kayıtları)."""
import unittest

from a2radar.protocol import Decoder
from a2radar.state import State, valid_coord

BRAX_13889 = "8f860304200092a224000002d5650cc86384d3c30052c846004cb34300ff0194a70794a7076400000064000000000000"
PLAYER_FORM = "e287011f000106546573746572ca902c00400240550dc8000096c20036c9468e5ca943dff00189708970b4120000b412"
NPC_13876 = "c192020400b0a61700c02b09c800b081c56319d4460000000000000000010101f9323ac5a10100000000000000000000"
MOVE_14127 = "84e10202c0f50dc8140b16c40032ca4600000000000000000000000000d6974601"
TAIL_14378 = "8f8603011301e5075f0a1027000000000000fc113bc5a1010000e7520ad48009010006bb0dc8e5ebd842006ec946"


class RealPacketTests(unittest.TestCase):
    def setUp(self):
        self.s = State()
        self.d = Decoder(self.s)

    def ents(self):
        return {e.eid: e for e in self.s.entities.snapshot()}

    def test_monster_spawn_13889(self):
        self.d.packet(13889, bytes.fromhex(BRAX_13889))
        e = self.ents()[49935]
        self.assertEqual(e.npc_id, 2400914)
        self.assertAlmostEqual(e.x / 100, -1438, delta=1)

    def test_player_form_13889(self):
        self.d.packet(13889, bytes.fromhex(PLAYER_FORM))
        e = self.ents()[17378]
        self.assertEqual(e.npc_id, -1)
        self.assertAlmostEqual(e.x / 100, -1447, delta=2)
        self.assertAlmostEqual(e.z / 100, 257.5, delta=2)

    def test_spawn_13876(self):
        self.d.packet(13876, bytes.fromhex(NPC_13876))
        e = self.ents()[35137]
        self.assertEqual(e.npc_id, 1550000)
        self.assertAlmostEqual(e.z / 100, 271, delta=1)

    def test_move_14127_offset(self):
        self.d.packet(14127, bytes.fromhex(MOVE_14127))
        e = self.ents()[45188]
        self.assertAlmostEqual(e.x / 100, -1453.7, delta=1)
        self.assertAlmostEqual(e.z / 100, 258, delta=2)

    def test_tail_packet_does_not_move_other_entity(self):
        self.d.packet(13889, bytes.fromhex(BRAX_13889))
        self.d.packet(14378, bytes.fromhex(TAIL_14378))   # konum başka varlığa (oyuncu) ait
        e = self.ents()[49935]
        self.assertAlmostEqual(e.x / 100, -1438, delta=1)
        self.assertAlmostEqual(e.y / 100, -4, delta=1)

    def test_origin_rejected(self):
        self.d.packet(13889, bytes.fromhex(BRAX_13889))
        self.s.entities.move(49935, 0.0, 0.0, 0.0)
        self.s.entities.move(49935, 1e-26, 2e-27, 0.0)
        e = self.ents()[49935]
        self.assertAlmostEqual(e.x / 100, -1438, delta=1)         # sıfıra atlamadı
        self.assertTrue(valid_coord(-143800.0, -400.0, 25600.0))


if __name__ == "__main__":
    unittest.main()
