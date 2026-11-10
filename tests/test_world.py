import unittest

from a2radar.entities import Labels
from a2radar.protocol import Decoder
from a2radar.state import State
from a2radar.world import World

BRAX = "8f860304200092a224000002d5650cc86384d3c30052c846004cb34300ff0194a70794a7076400000064000000000000"
ODYLE = "c192020400b0a61700c02b09c800b081c56319d4460000000000000000010101f9323ac5a10100000000000000000000"
PLAYER_FORM = "e287011f000106546573746572ca902c00400240550dc8000096c20036c9468e5ca943dff00189708970b4120000b412"


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.st = State()
        self.st.set_pos(-144000.0, -400.0, 25600.0)
        self.w = World(labels=Labels("nonexistent_dir/e.json"), state=self.st)
        d = Decoder(self.st)
        for op, h in ((13889, BRAX), (13876, ODYLE), (13889, PLAYER_FORM)):
            d.packet(op, bytes.fromhex(h))

    def test_view_labels_and_distance_order(self):
        v = self.w.view()
        names = [s.name for s in v.entities]
        self.assertEqual(names, ["Brax", "Odyle"])        # oyuncu (Tester) varsayılan olarak gizli
        self.assertTrue(v.entities[0].dist_m < v.entities[1].dist_m)
        self.assertEqual(v.entities[0].kind, "canavar")
        self.assertEqual(v.entities[1].kind, "kaynak")
        self.assertAlmostEqual(v.entities[1].dz_m, 15, delta=1)

    def test_nearest_filters(self):
        self.assertEqual([s.name for s in self.w.nearest(kind="kaynak")], ["Odyle"])
        self.assertEqual(self.w.nearest(name="brax")[0].npc_id, 2400914)
        self.assertEqual(self.w.nearest(kind="kaynak", radius_m=5), [])

    def test_counts_and_players(self):
        self.assertEqual(self.w.counts(), {"Brax": 1, "Odyle": 1})
        self.assertEqual(len(self.w.view(include_players=True).entities), 3)

    def test_no_position(self):
        w = World(labels=Labels("nonexistent_dir/e.json"), state=State())
        Decoder(w.state).packet(13889, bytes.fromhex(BRAX))
        s = w.view().entities[0]
        self.assertIsNone(s.dist_m)
        self.assertEqual(s.name, "Brax")


if __name__ == "__main__":
    unittest.main()
