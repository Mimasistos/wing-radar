import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a2radar import entities as E
from a2radar.protocol import Decoder
from a2radar.state import State
from test_protocol import CUBE, f3, frame, spawn, varint, zone_enter

WOLF = 2000123


class Feed:
    """Akış sırası takibi olan küçük yardımcı."""

    def __init__(self, state):
        self.s, self.d, self.seq = state, Decoder(state, 13328), 1000

    def __call__(self, data: bytes):
        self.d.feed("1.1.1.1", 13328, "2.2.2.2", 5, self.seq, data)
        self.seq += len(data)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.s = State()
        self.feed = Feed(self.s)

    def test_non_cube_spawn_is_tracked_but_not_a_cube(self):
        self.feed(spawn(10, WOLF, 5000, 6000, 100))
        ents = self.s.entities.snapshot()
        self.assertEqual([(e.eid, e.npc_id, e.x, e.y, e.z) for e in ents], [(10, WOLF, 5000.0, 6000.0, 100.0)])
        self.assertEqual(self.s.snapshot().cubes, [])

    def test_cube_spawn_is_tracked_in_both(self):
        self.feed(spawn(11, CUBE, 1, 2, 3))
        self.assertEqual(len(self.s.snapshot().cubes), 1)
        self.assertEqual(self.s.entities.snapshot()[0].npc_id, CUBE)

    def test_invalid_coordinates_are_not_tracked(self):
        self.feed(spawn(12, WOLF, float("nan"), 0, 0))
        self.feed(spawn(13, WOLF, 9e9, 0, 0))
        self.assertEqual(self.s.entities.snapshot(), [])

    def test_move_updates_known_entity_only(self):
        self.feed(spawn(10, WOLF, 1, 1, 1))
        for eid, x in ((10, 777.0), (99, 555.0)):                      # 99 bilinmiyor: kayıt oluşmaz
            body = varint(eid) + bytes(1) + f3(x, 0, 0)          # 14107: konum n+1
            self.feed(frame(14107, body))
        ents = {e.eid: e for e in self.s.entities.snapshot()}
        self.assertEqual(ents[10].x, 777.0)
        self.assertNotIn(99, ents)

    def test_despawn_and_zone_change_clear(self):
        self.feed(spawn(10, WOLF, 1, 1, 1))
        self.feed(spawn(11, WOLF, 10, 0, 0))
        self.feed(frame(13877, varint(10)))
        self.assertEqual([e.eid for e in self.s.entities.snapshot()], [11])
        self.feed(zone_enter(5000, 0, 0, 0))
        self.assertEqual(self.s.entities.snapshot(), [])

    def test_sample_keeps_first_raw_payload_per_npc(self):
        self.feed(spawn(10, WOLF, 1, 1, 1))
        self.feed(spawn(11, WOLF, 9, 9, 9))
        self.assertEqual(len(self.s.entities.samples), 1)
        smp = self.s.entities.samples[WOLF]
        self.assertEqual(smp["op"], 13876)
        self.assertEqual(smp["pos"], [1.0, 1.0, 1.0])
        self.assertTrue(smp["hex"])

    def test_capacity_evicts_oldest(self):
        t = E.EntityTracker()
        t.MAX_ENTITIES = 3
        for i in range(5):
            t.spawn(100 + i, WOLF, i, 0, 0)
            t.move(100 + i, i, 0, 0)
        self.assertEqual(len(t.by_eid), 3)
        self.assertIn(104, t.by_eid)
        self.assertNotIn(100, t.by_eid)


class GroupingTests(unittest.TestCase):
    def ents(self):
        return [E.Entity(1, 5, 1000, 0, 0), E.Entity(2, 5, 3000, 0, 0), E.Entity(3, 6, 0, -2000, 500), E.Entity(4, 7, 90000, 0, 0)]

    def test_groups_count_nearest_and_bearing(self):
        g = E.group_entities(self.ents(), (0.0, 0.0, 0.0), radius_m=100)
        self.assertEqual([x["npc_id"] for x in g], [5, 6])                    # 7 yarıçap dışı; en yakın önce
        self.assertEqual(g[0]["count"], 2)
        self.assertEqual(g[0]["nearest_m"], 10.0)
        self.assertAlmostEqual(g[0]["bearing"], math.pi / 2)                  # +x = doğu
        self.assertEqual(E.compass(g[0]["bearing"]), "D")
        self.assertEqual(E.compass(g[1]["bearing"]), "K")                     # -y = kuzey
        self.assertEqual(g[1]["dz_m"], 5.0)

    def test_without_position_no_distances(self):
        g = E.group_entities(self.ents(), None)
        self.assertEqual(len(g), 3)
        self.assertTrue(all(x["nearest_m"] is None for x in g))

    def test_compass_wraps(self):
        self.assertEqual(E.compass(-0.1), "K")
        self.assertEqual(E.compass(math.pi), "G")
        self.assertEqual(E.compass(-math.pi / 2), "B")


class LabelTests(unittest.TestCase):
    def test_persist_validate_remove(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.json"
            lab = E.Labels(p)
            lab.set(WOLF, "  Orman kurdu ", "canavar")
            self.assertEqual(lab.get(WOLF), {"name": "Orman kurdu", "kind": "canavar"})
            self.assertEqual(E.Labels(p).get(WOLF)["name"], "Orman kurdu")        # diskten geri okunur
            for bad in (("", "canavar"), ("x" * 61, "canavar"), ("a", "bilinmeyen")):
                with self.assertRaises(ValueError):
                    lab.set(WOLF, *bad)
            lab.remove(WOLF)
            self.assertIsNone(E.Labels(p).get(WOLF))

    def test_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.json"
            p.write_text("{bozuk", "utf-8")
            self.assertEqual(E.Labels(p).data, {})

    def test_export_samples(self):
        with tempfile.TemporaryDirectory() as d:
            t = E.EntityTracker()
            t.spawn(1, WOLF, 1, 2, 3, 13876, b"\x01\x02\x03")
            lab = E.Labels(Path(d) / "e.json")
            lab.set(WOLF, "Kurt")
            out = json.loads(E.export_samples(t, lab, Path(d) / "s.json").read_text("utf-8"))
            self.assertEqual(out[str(WOLF)]["hex"], "010203")
            self.assertEqual(out[str(WOLF)]["label"], "Kurt")


if __name__ == "__main__":
    unittest.main()
