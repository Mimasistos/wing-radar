import json
import math
import tempfile
import unittest
from pathlib import Path

from a2radar import catalog, profiles, routes
from a2radar.entities import KNOWN, Labels
from a2radar.spawndb import SpawnDB


def tmp():
    return Path(tempfile.mkdtemp())


class SpawnDBTests(unittest.TestCase):
    def test_dedup_and_persist(self):
        p = tmp() / "s.json"
        db = SpawnDB(p)
        db.record(2400914, -143800, -400, 25600)
        db.record(2400914, -143700, -450, 25600)       # 1 m içinde: aynı nokta
        db.record(2400914, -144800, -1900, 25900)      # farklı nokta
        db.record(1000548, 1, 1, 1)                    # küp kaydedilmez
        db.record(0, 1, 1, 1)
        self.assertEqual(db.count(2400914), 2)
        self.assertEqual(db.npc_ids(), [2400914])
        self.assertEqual(sorted(pt[3] for pt in db.points([2400914])), [1, 2])
        db.flush(force=True)
        self.assertEqual(SpawnDB(p).count(2400914), 2)

    def test_corrupt_file_is_ignored(self):
        p = tmp() / "s.json"
        p.write_text("{bozuk", "utf-8")
        self.assertEqual(SpawnDB(p).npc_ids(), [])

    def test_cap(self):
        db = SpawnDB(tmp() / "s.json")
        for i in range(SpawnDB.MAX_PER_NPC + 50):
            db.record(5, i * 1000.0, 0, 0)
        self.assertEqual(db.count(5), SpawnDB.MAX_PER_NPC)

    def test_import_log(self):
        d = tmp()
        (d / "l.json").write_text(json.dumps({"spawn_log": [
            {"event": "spawn", "npc": 2400914, "pos_m": [-1438, -4, 256]},
            {"event": "despawn", "npc": 2400914, "pos_m": [-1438, -4, 256]},
            {"event": "spawn", "npc": 2400914, "pos_m": [0, 0, 0]},
            {"event": "spawn", "npc": 1550000, "pos_m": [-1405, -42, 271]}]}), "utf-8")
        db = SpawnDB(d / "s.json")
        self.assertEqual(db.import_log(d / "l.json"), 2)
        self.assertEqual(db.npc_ids(), [1550000, 2400914])
        self.assertEqual(db.import_log(d / "yok.json"), 0)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.db = SpawnDB(tmp() / "s.json")
        self.db.record(2400914, -143800, -400, 25600)
        self.db.record(777777, 5000, 5000, 100)         # etiketsiz
        self.labels = Labels(tmp() / "e.json")

    def test_kinds_and_filter(self):
        mons = catalog.build(self.labels, self.db, "canavar")
        res = catalog.build(self.labels, self.db, "kaynak")
        self.assertIn("Brax", [e.name for e in mons])
        self.assertIn("Odyle", [e.name for e in res])
        self.assertNotIn("Odyle", [e.name for e in mons])
        brax = next(e for e in mons if e.name == "Brax")
        self.assertEqual(brax.spawns, 1)
        self.assertEqual([e.name for e in catalog.filter_entries(mons, "cora")], ["Aberrant Coradon", "Young Coradon"])
        self.assertEqual(catalog.filter_entries(mons, "  "), mons)

    def test_unlabeled_and_user_label_override(self):
        un = catalog.build(self.labels, self.db, "")
        self.assertEqual([e.name for e in un], ["NPC 777777"])
        self.labels.data[777777] = {"name": "Kurt", "kind": "canavar"}
        self.assertIn("Kurt", [e.name for e in catalog.build(self.labels, self.db, "canavar")])
        self.assertEqual(catalog.build(self.labels, self.db, ""), [])

    def test_papis_ids_share_one_row_and_ignored_hidden(self):
        self.db.record(2000927, 1000, 1000, 100)
        self.db.record(2000929, 90000, 1000, 100)
        self.db.record(1300865, 5000, 5000, 100)            # yok sayılan kaynak: kaydedilmez
        mons = catalog.build(self.labels, self.db, "canavar")
        papis = [e for e in mons if e.name == "Faded Papis"]
        self.assertEqual(len(papis), 1)
        self.assertEqual(papis[0].npc_ids, [2000927, 2000929])
        self.assertEqual(papis[0].spawns, 2)
        self.assertEqual(self.db.count(1300865), 0)
        for n in (1300014, 1300019, 1300020, 1100001, 1300007, 1300006, 1300005, 1300311, 1300310, 1300413):
            self.db.record(n, 5000, 5000, 100)
            self.assertEqual(self.db.count(n), 0)
        for n in ("Draconute Superior Combatant", "Draconute Tinkerer"):
            self.assertIn(n, [e.name for e in mons])
        for n in (2400031, 1400276, 1100006):
            self.db.record(n, 5000, 5000, 100)
            self.assertEqual(self.db.count(n), 0)
        self.assertNotIn("NPC 1300865", [e.name for e in catalog.build(self.labels, self.db, "")])
        for n in ("Withered Armadon", "Ashen Spider"):
            self.assertIn(n, [e.name for e in mons])

    def test_ids_for(self):
        mons = catalog.build(self.labels, self.db, "canavar")
        self.assertEqual(catalog.ids_for(mons, ["Brax"]), [2400914])
        self.assertEqual(set(KNOWN) >= {2400914}, True)


class ProfileTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        st = profiles.ProfileStore(tmp())
        p = profiles.blank("Farm A")
        p["routes"]["R1"] = [{"x_m": 1.0, "y_m": 2.0, "label": "a"}]
        p["active_route"] = "R1"
        p["attack_skills"].append({"name": "Fireball", "key": "1"})
        st.save(p)
        q = st.load("Farm A")
        self.assertEqual(q["routes"]["R1"][0]["x_m"], 1.0)
        self.assertEqual(q["active_route"], "R1")
        self.assertEqual(st.names(), ["Farm A"])
        st.copy("Farm A", "Farm B")
        self.assertEqual(st.names(), ["Farm A", "Farm B"])
        st.delete("Farm A")
        self.assertEqual(st.names(), ["Farm B"])
        for bad in ("", "../x", "a/b", "x" * 41, ".."):
            with self.assertRaises(ValueError):
                st.save({**profiles.blank("ok"), "name": bad})

    def test_corrupt_profile_gets_defaults(self):
        d = tmp()
        (d / "Bozuk.json").write_text(json.dumps({"routes": {"r": [{"x_m": "a"}, 5]}, "active_route": "yok",
                                                  "gather": {"radius_m": "x"}, "attack_skills": "no"}), "utf-8")
        p = profiles.ProfileStore(d).load("Bozuk")
        self.assertEqual(p["routes"], {"r": []})
        self.assertEqual(p["active_route"], "")
        self.assertEqual(p["gather"]["radius_m"], 150.0)
        self.assertEqual(p["attack_skills"], [])


class RouteTests(unittest.TestCase):
    PTS = [{"x_m": x, "y_m": y} for x, y in ((100, 0), (10, 0), (50, 0), (200, 0), (150, 5))]

    def test_order_nearest_is_sorted_and_not_longer(self):
        out = routes.order_nearest((0, 0), self.PTS)
        self.assertEqual([p["x_m"] for p in out], [10, 50, 100, 150, 200])
        self.assertLessEqual(routes.length_m(out), routes.length_m(self.PTS))

    def test_walker_advances_and_finishes(self):
        w = routes.Walker([{"x_m": 0, "y_m": -100}, {"x_m": 100, "y_m": -100}])
        p, dist, ang = w.target((0, 0))
        self.assertAlmostEqual(dist, 100)
        self.assertAlmostEqual(ang, 0.0)                    # oyun -y = kuzey
        p, dist, _ = w.target((0, -95))                     # 5 m kaldı: ulaşıldı, sıradaki
        self.assertEqual(p["x_m"], 100)
        self.assertIsNone(w.target((100, -100)))
        self.assertTrue(w.done)

    def test_loop_does_not_hang(self):
        w = routes.Walker([{"x_m": 0, "y_m": 0}], loop=True)
        self.assertIsNotNone(w.target((0, 0)) or True)       # tek durak, üstünde: sonsuz döngü olmamalı
        self.assertIsNone(routes.Walker().target((0, 0)))
        self.assertIsNone(routes.Walker([{"x_m": 1, "y_m": 1}]).target(None))


if __name__ == "__main__":
    unittest.main()
