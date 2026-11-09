import math
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2radar import mapcal
from a2radar.mapcal import MapGuide, cover, fit, neighbors, order, route_length
from a2radar.state import Cube

A = 250 * complex(math.cos(math.radians(30)), math.sin(math.radians(30)))   # 250 cm/px, 30° dönük
B = complex(-120_000, 45_000)


def world(px, py, flip=False):
    z = A * complex(px, -py if flip else py) + B
    return z.real, z.imag


LM = [["a", 500, 500], ["b", 3000, 700], ["c", 1500, 3500], ["d", 3500, 3600]]
CUBES = [[1000, 1000], [2000, 2000], [3000, 3000], [3500, 500]]


class FitTests(unittest.TestCase):
    def test_recovers_transform(self):
        pts = [(px, py, *world(px, py)) for _, px, py in LM[:3]]
        a, b, flip, rms = fit(pts)
        self.assertFalse(flip)
        self.assertAlmostEqual(abs(a - A), 0, places=4)
        self.assertLess(rms, 1e-3)

    def test_detects_reflection(self):
        pts = [(px, py, *world(px, py, True)) for _, px, py in LM[:3]]
        self.assertTrue(fit(pts)[2])

    def test_noisy_anchors_still_close(self):
        random.seed(1)
        pts = [(px, py, gx + random.uniform(-800, 800), gy + random.uniform(-800, 800))
               for _, px, py in LM for gx, gy in [world(px, py)]]
        a, b, _, rms = fit(pts)
        self.assertLess(rms, 1500)
        self.assertLess(abs(a - A) / abs(A), 0.05)


class ViewTests(unittest.TestCase):
    def _check(self, flip):
        tmp = tempfile.TemporaryDirectory()
        g = MapGuide(CUBES, LM, store=Path(tmp.name) / "v.json")
        for name, px, py in LM[:3]:
            gx, gy = world(px, py, flip)
            g.add_anchor_px(name, px, py, gx, gy)
        self.assertEqual(g.tf[2], flip)
        # oyuncu bir noktanın üstünde; görünümde o noktanın çıktı pikseli merkez olmalı
        pgx, pgy = world(2000, 2000, flip)
        s, cx, cy, k = 0.01, 100, 80, 0.5
        c = g.view_coeffs(pgx, pgy, s, cx, cy, k)
        # merkez piksel -> harita (2000, 2000) * k
        self.assertAlmostEqual(c[0] * cx + c[1] * cy + c[2], 2000 * k, places=4)
        self.assertAlmostEqual(c[3] * cx + c[4] * cy + c[5], 2000 * k, places=4)
        # 5000 cm kuzey (game -y) -> ekranda 50 px yukarı; haritadaki karşılığı to_map ile tutmalı
        u, v = cx, cy - 5000 * s
        mx, my = g.to_map(pgx, pgy - 5000)
        self.assertAlmostEqual(c[0] * u + c[1] * v + c[2], mx * k, places=4)
        self.assertAlmostEqual(c[3] * u + c[4] * v + c[5], my * k, places=4)
        tmp.cleanup()

    def test_view_coeffs_plain(self):
        self._check(False)

    def test_view_coeffs_flipped(self):
        self._check(True)

    def test_anchor_px_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(CUBES, LM, store=Path(d) / "a.json")
            for name, px, py in LM:
                g.add_anchor_px(name, px, py, *world(px, py))
            g.add_anchor_px("yanlis", 2500, 2500, *world(2800, 2500))   # 300 px = 750 m sapma
            errs = g.anchor_errors()
            self.assertEqual(max(errs, key=errs.get), "yanlis")
            with self.assertRaises(ValueError):
                g.add_anchor_px("x", 5000, 0, 0, 0)

    def test_suggested_names_present(self):
        self.assertIn("Gravekeeper Camp", mapcal.SUGGESTED)
        self.assertEqual(len(mapcal.SUGGESTED), 6)


class DataLookupTests(unittest.TestCase):
    def test_falls_back_to_appdata_folder(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as app, tempfile.TemporaryDirectory() as pkg:
            (Path(app) / "A2Radar").mkdir()
            (Path(app) / "A2Radar" / "x.json").write_text("[[1, 2]]", "utf-8")
            with mock.patch.dict(os.environ, {"APPDATA": app}), mock.patch.object(mapcal, "DATA", Path(pkg)):
                self.assertEqual(mapcal._load("x.json"), [[1, 2]])
                with self.assertRaises(FileNotFoundError):
                    mapcal._load("yok.json")

    def test_package_dir_wins_over_appdata(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as app, tempfile.TemporaryDirectory() as pkg:
            (Path(app) / "A2Radar").mkdir()
            (Path(app) / "A2Radar" / "x.json").write_text("[1]", "utf-8")
            (Path(pkg) / "x.json").write_text("[2]", "utf-8")
            with mock.patch.dict(os.environ, {"APPDATA": app}), mock.patch.object(mapcal, "DATA", Path(pkg)):
                self.assertEqual(mapcal._load("x.json"), [2])


class PriorAndFilterTests(unittest.TestCase):
    def test_prior_makes_guide_ready_without_anchors(self):
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(CUBES, LM, store=Path(d) / "p.json", prior=mapcal.PRIOR)
            self.assertTrue(g.ready and g.default_tf and g.approximate)
            self.assertEqual(g.to_game(2048, 2048), (0.0, 0.0))
            self.assertEqual(g.to_game(2058, 2048), (2000.0, 0.0))        # 10 px = 20 m
            self.assertAlmostEqual(g.cm_per_px, 200)
            self.assertIsNone(g.solve())                                    # anchor yok: sonuç yok ama önsel aktif

    def test_regularize_keeps_prior_scale_with_noisy_clicks(self):
        random.seed(3)
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(CUBES, LM, store=Path(d) / "p.json", prior=mapcal.PRIOR)
            for name, px, py in LM[:3]:
                g.add_anchor_px(name, px + random.uniform(-15, 15), py + random.uniform(-15, 15),
                                (px - 2048) * 200, (py - 2048) * 200)
            self.assertFalse(g.default_tf)
            self.assertAlmostEqual(abs(g.tf[0]), 200, places=6)             # ölçek önsele sabitlendi
            self.assertLess(g.rms_m, 40)

    def test_far_from_prior_uses_free_fit(self):
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(CUBES, LM, store=Path(d) / "p.json", prior=mapcal.PRIOR)
            for name, px, py in LM[:3]:
                g.add_anchor_px(name, px, py, *world(px, py))                # 250 cm/px, 30° dönük
            self.assertAlmostEqual(abs(g.tf[0]), 250, places=3)

    def test_land_filter_removes_points_from_plan(self):
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(CUBES, LM, store=Path(d) / "p.json", prior=mapcal.PRIOR)
            g.sync_order = True
            pos = (0.0, 0.0, 0)
            self.assertEqual(g.summary(pos, 10_000)[1], 4)
            g.apply_land_filter(lambda mx, my: mx < 2500)                    # (3000,3000) ve (3500,500) dışarıda
            self.assertEqual(g.off, {2, 3})
            self.assertEqual(g.summary(pos, 10_000)[1], 2)
            g.apply_land_filter(None)
            self.assertEqual(g.summary(pos, 10_000)[1], 4)


class OrderQualityTests(unittest.TestCase):
    def test_two_opt_scales_and_beats_nearest_neighbour(self):
        random.seed(11)
        pts = [(random.uniform(0, 800_000), random.uniform(0, 800_000)) for _ in range(900)]
        start = (0.0, 0.0)
        # saf en yakın komşu
        left, cur, nn = set(range(len(pts))), start, []
        while left:
            j = min(left, key=lambda i: (pts[i][0] - cur[0]) ** 2 + (pts[i][1] - cur[1]) ** 2)
            nn.append(j)
            left.discard(j)
            cur = pts[j]
        t0 = time.time()
        opt = mapcal.order(start, pts, budget=2.0)
        dt = time.time() - t0
        self.assertEqual(sorted(opt), list(range(900)))
        self.assertLess(dt, 4.0)
        self.assertLess(route_length(start, pts, opt), route_length(start, pts, nn) * 0.92)

    def test_quick_order_is_permutation_and_sane(self):
        random.seed(5)
        pts = [(random.uniform(0, 100_000), random.uniform(0, 100_000)) for _ in range(500)]
        q = mapcal.quick_order((0.0, 0.0), pts)
        self.assertEqual(sorted(q), list(range(500)))
        shuffled = list(range(500))
        random.shuffle(shuffled)
        self.assertLess(route_length((0, 0), pts, q), route_length((0, 0), pts, shuffled) * 0.2)

    def test_async_reorder_is_adopted_without_blocking(self):
        random.seed(2)
        cubes = [[random.uniform(0, 4096), random.uniform(0, 4096)] for _ in range(600)]
        with tempfile.TemporaryDirectory() as d:
            g = MapGuide(cubes, LM, store=Path(d) / "a.json", prior=mapcal.PRIOR)
            g.reorder_budget = 1.0
            pos = (-300_000.0, -300_000.0, 0)
            t0 = time.time()
            first = g.summary(pos, 5000)
            self.assertLess(time.time() - t0, 0.6)                          # arayüzü bekletmez
            before = first[2]
            g._worker.join(10)
            after = g.summary(pos, 5000)[2]                                 # sonuç benimsendi
            self.assertLess(after, before * 0.9)


class RouteMathTests(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.pts = [(random.uniform(0, 40000), random.uniform(0, 40000)) for _ in range(300)]

    def test_neighbors_match_bruteforce(self):
        r = 5000
        nb = neighbors(self.pts, r)
        for i in (0, 50, 299):
            brute = sorted(j for j, p in enumerate(self.pts) if math.dist(p, self.pts[i]) <= r)
            self.assertEqual(sorted(nb[i]), brute)

    def test_cover_is_complete_and_within_radius(self):
        r = 6000
        stops = cover(self.pts, r)
        seen = set()
        for c, got in stops:
            for j in got:
                self.assertLessEqual(math.dist(self.pts[c], self.pts[j]), r + 1e-6)
            seen |= set(got)
        self.assertEqual(seen, set(range(len(self.pts))))
        self.assertLess(len(stops), len(self.pts) / 3)

    def test_cover_respects_alive(self):
        stops = cover(self.pts, 6000, alive=[0, 1, 2])
        self.assertEqual(sorted(j for _, g in stops for j in g), [0, 1, 2])

    def test_order_beats_input_order_and_is_permutation(self):
        stops = self.pts[:60]
        idx = order((0, 0), stops)
        self.assertEqual(sorted(idx), list(range(60)))
        self.assertLess(route_length((0, 0), stops, idx), route_length((0, 0), stops, list(range(60))) * 0.6)

    def test_order_line_is_monotone(self):
        stops = [(x * 1000, 0) for x in (5, 1, 4, 2, 3)]
        self.assertEqual([stops[i][0] for i in order((0, 0), stops)], [1000, 2000, 3000, 4000, 5000])


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.g = MapGuide(CUBES, LM, store=Path(self.tmp.name) / "cal.json")
        for name, px, py in LM[:3]:
            self.g.add_anchor(name, *world(px, py))

    def tearDown(self):
        self.tmp.cleanup()

    def test_ready_rms_and_inverse(self):
        self.assertTrue(self.g.ready)
        self.assertFalse(self.g.approximate)
        self.assertLess(self.g.rms_m, 0.01)
        mx, my = self.g.to_map(*world(1234, 2345))
        self.assertAlmostEqual(mx, 1234, places=3)
        self.assertAlmostEqual(my, 2345, places=3)
        self.assertAlmostEqual(self.g.cm_per_px, 250, places=3)

    def test_next_stop_is_nearest_cluster_and_bearing(self):
        gx, gy = world(2000, 2000)
        pos = (gx, gy - 3000, 0)               # aday 30 m güneyde
        d, bearing, stops, left, length, _ = self.g.next_stop(pos, 5000)
        self.assertGreater(stops, 0)
        self.assertEqual(left, 4)
        self.assertGreater(length, 0)
        self.assertGreaterEqual(d, 0)

    def test_visit_removes_points_and_replans(self):
        gx, gy = world(2000, 2000)
        before = self.g.summary((gx, gy, 0), 1000)
        self.g.update_visited((gx + 500, gy, 0), 1000, fresh=True)
        self.assertEqual(len(self.g.visited), 1)
        after = self.g.summary((gx, gy, 0), 1000)
        self.assertEqual(after[1], before[1] - 1)
        self.g.update_visited((gx, gy + 99999, 0), 1000, fresh=False)
        self.assertEqual(len(self.g.visited), 1)

    def test_live_cube_excluded(self):
        gx, gy = world(2000, 2000)
        live = [Cube(1, 1, gx + 300, gy, 0)]
        self.assertEqual(self.g.summary((gx, gy, 0), 1000, live)[1], 3)

    def test_route_empty_when_all_visited(self):
        self.g.update_visited(world(2000, 2000) + (0,), 10 ** 9, fresh=True)
        self.assertIsNone(self.g.next_stop(world(0, 0) + (0,), 1000))

    def test_persists_and_reset(self):
        g2 = MapGuide(CUBES, LM, store=Path(self.tmp.name) / "cal.json")
        self.assertTrue(g2.ready)
        g2.clear()
        self.assertFalse(g2.ready)

    def test_close_anchors_rejected(self):
        g = MapGuide(CUBES, LM, store=Path(self.tmp.name) / "x.json")
        g.add_anchor("a", *world(500, 500))
        g.add_anchor("b", *world(3000, 700))
        self.assertTrue(g.approximate)
        g.clear()
        g.add_anchor("a", *world(500, 500))
        g.anchors.append({"name": "z", "px": 520, "py": 520, "gx": world(520, 520)[0], "gy": world(520, 520)[1]})
        self.assertIsNone(g.solve())


@unittest.skipUnless((mapcal.DATA / "altgard_cubes.json").exists() and (mapcal.DATA / "landmarks.json").exists(),
                     "veri dosyaları yok (depoda bulunmaz, a2radar/data/README.md)")
class RealDataTests(unittest.TestCase):
    def test_real_data_and_plan_speed(self):
        tmp = tempfile.TemporaryDirectory()
        g = MapGuide(store=Path(tmp.name) / "r.json")
        self.assertGreater(len(g.cubes), 900)
        for name, x, y in g.landmarks:
            self.assertTrue(0 <= x <= 4096 and 0 <= y <= 4096, name)
        # Sahte kalibrasyon: 1 px = 3 m (gerçek ölçek bilinmiyor)
        for n in ("Logger's Rest Ruins", "Graverobber Den", "Abyss Portal"):
            lm = g.landmark(n)
            g.add_anchor(n, lm[1] * 300, lm[2] * 300)
        t = time.time()
        r = g.summary((600_000, 600_000, 0), 10_000)     # R = 100 m
        dt = time.time() - t
        stops, left, length = r
        print(f"\n  R=100m: {stops} durak, {left} aday, {length / 1000:.1f} km, plan {dt:.2f}s")
        self.assertGreater(left, 900)
        self.assertLess(dt, 2)
        t = time.time()
        for k in range(20):
            g.next_stop((600_000 + k * 500, 600_000, 0), 10_000)
        self.assertLess((time.time() - t) / 20, 0.05)   # tekrar çağrılar ucuz
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
