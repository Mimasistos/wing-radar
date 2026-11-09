"""Harita (piksel) <-> oyun koordinatı dönüşümü ve küp arama rotası.

Dönüşüm: g = a*p + b (karmaşık sayılar; a = ölçek+dönüş), gerekirse yansıma (flip).
Kullanıcı bilinen yerlerin (kale, zindan girişi...) yanında durup konumunu kaydeder; en az 3 yer
gerekir. 2 yerde dönüşüm 'tahmini' sayılır (yansıma ayırt edilemez).

Rota: sunucu küp paketini yalnızca oyuncuya yakınken gönderir (tarama yarıçapı R). Bu yüzden her
noktaya değil, tüm noktaları R içinde bırakan az sayıda 'durak'a gitmek yeter:
  1. Açgözlü küme örtüsü: en çok kapsanmamış noktayı kapsayan nokta durak olur.
  2. Duraklar en yakın komşu + 2-opt ile sıralanır (açık yol, başlangıç = oyuncu).
  3. Oyuncu R içine girince ve gerçek küp paketi yoksa o noktalar 'boş' sayılıp rotadan düşer.
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

DATA = Path(__file__).parent / "data"
LIVE_MATCH_CM = 1500.0        # gerçek küpün 15 m yakınındaki aday = aynı küp
MIN_SPREAD_PX = 300.0


SUGGESTED = ["Gravekeeper Camp", "Moslan Forest Dry Sade", "Faded Stump",
             "Basfelt Ruins Cliff", "Outskirts Stall", "Abandoned Campsite"]


# Varsayılan dönüşüm (kullanıcının 3 yerlik kalibrasyonundan: 2.01 m/piksel, dönme ~0, yansıma yok, 3 yerde
# 1.2 m rms): dünya (cm) = 200 * (piksel - 2048). Kalibrasyonsuz tahmini çalışır, kalibrasyon iyileştirir.
PRIOR = (200 + 0j, complex(-200 * 2048, -200 * 2048), False)


def store_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "A2Radar" / "mapcal.json"


def data_dirs():
    """Veri dosyası aranan klasörler: paket içi data/ ve %APPDATA%/A2Radar (kurulu pakette veri buraya konur)."""
    return [DATA, Path(os.environ.get("APPDATA", Path.home())) / "A2Radar"]


def _load(name):
    for d in data_dirs():
        if (d / name).is_file():
            return json.loads((d / name).read_text("utf-8"))
    raise FileNotFoundError(name)


def fit(anchors):
    """anchors: [(px, py, gx, gy)]. -> (a, b, flip, rms_cm) veya None."""
    n = len(anchors)
    if n < 2:
        return None
    best = None
    for flip in (False, True):
        if flip and n < 3:
            continue
        q = [complex(px, -py if flip else py) for px, py, _, _ in anchors]
        g = [complex(gx, gy) for _, _, gx, gy in anchors]
        qm, gm = sum(q) / n, sum(g) / n
        den = sum(abs(v - qm) ** 2 for v in q)
        if den == 0:
            return None
        a = sum((gi - gm) * (qi - qm).conjugate() for gi, qi in zip(g, q)) / den
        b = gm - a * qm
        rms = math.sqrt(sum(abs(a * qi + b - gi) ** 2 for qi, gi in zip(q, g)) / n)
        if best is None or rms < best[3]:
            best = (a, b, flip, rms)
    return best


def neighbors(pts, radius):
    """pts: [(x, y)] -> her nokta için radius içindeki indeksler (kendisi dahil). Izgara ile."""
    cell = max(radius, 1.0)
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    r2 = radius * radius
    out = []
    for x, y in pts:
        cx, cy = int(x // cell), int(y // cell)
        out.append([j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((cx + dx, cy + dy), ())
                    if (pts[j][0] - x) ** 2 + (pts[j][1] - y) ** 2 <= r2])
    return out


def cover(pts, radius, alive=None):
    """Açgözlü örtü (tembel öncelik kuyruğu). -> [(merkez_idx, [kapsanan_idx...])]."""
    import heapq
    alive = set(range(len(pts))) if alive is None else set(alive)
    nb = neighbors(pts, radius)
    count = [sum(1 for j in nb[i] if j in alive) for i in range(len(pts))]
    heap = [(-c, i) for i, c in enumerate(count) if c]
    heapq.heapify(heap)
    stops = []
    while alive and heap:
        c, i = heapq.heappop(heap)
        cur = sum(1 for j in nb[i] if j in alive)
        if cur == 0:
            continue
        if cur < -c:
            heapq.heappush(heap, (-cur, i))
            continue
        got = [j for j in nb[i] if j in alive]
        stops.append((i, got))
        alive.difference_update(got)
    return stops


def _knn(pts, k):
    """Her nokta için en yakın k komşu (ızgara ile)."""
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    cell = max(w / max(1.0, math.sqrt(n)) * 1.5, 1.0)
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    out = []
    for i, (x, y) in enumerate(pts):
        cx, cy = int(x // cell), int(y // cell)
        r = 1
        while True:
            cand = [j for dx in range(-r, r + 1) for dy in range(-r, r + 1) for j in grid.get((cx + dx, cy + dy), ()) if j != i]
            if len(cand) >= k or r > 40:
                break
            r += 1
        cand.sort(key=lambda j: (pts[j][0] - x) ** 2 + (pts[j][1] - y) ** 2)
        out.append(cand[:k])
    return out


def order(start, stops, budget=1.0, k=8):
    """start (x, y), stops [(x, y)] -> sıralı indeks listesi.

    En yakın komşu başlangıcı + komşu listeli 2-opt (açık yol; başlangıç sabit). Büyük listelerde de
    çalışır; `budget` saniyede durur.
    """
    import time
    n = len(stops)
    if n <= 1:
        return list(range(n))
    left, cur, path = set(range(n)), start, []
    while left:
        j = min(left, key=lambda i: (stops[i][0] - cur[0]) ** 2 + (stops[i][1] - cur[1]) ** 2)
        path.append(j)
        left.discard(j)
        cur = stops[j]
    if n < 4:
        return path
    pts = [start] + list(stops)                 # düğüm 0 = başlangıç, düğüm i+1 = stops[i]
    tour = [0] + [j + 1 for j in path]
    L = len(tour)
    pos = [0] * L
    for i, v in enumerate(tour):
        pos[v] = i
    nb = _knn(pts, k)
    hyp = math.hypot

    def d(a, b):
        return hyp(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])

    deadline = time.perf_counter() + budget
    improved = True
    while improved and time.perf_counter() < deadline:
        improved = False
        for i in range(L):
            a = tour[i]
            for c in nb[a]:
                j = pos[c]
                lo, hi = (i, j) if i < j else (j, i)
                if hi - lo < 2:
                    continue
                A, B, C = tour[lo], tour[lo + 1], tour[hi]
                D = tour[hi + 1] if hi + 1 < L else None
                old = d(A, B) + (d(C, D) if D is not None else 0.0)
                new = d(A, C) + (d(B, D) if D is not None else 0.0)
                if new + 1e-9 < old:
                    seg = tour[lo + 1:hi + 1]
                    seg.reverse()
                    tour[lo + 1:hi + 1] = seg
                    for q in range(lo + 1, hi + 1):
                        pos[tour[q]] = q
                    improved = True
                    break
            if time.perf_counter() > deadline:
                break
    return [v - 1 for v in tour[1:]]


def route_length(start, stops, idx):
    pts = [start] + [stops[i] for i in idx]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _hilbert_d(n, x, y):
    d, s = 0, n // 2
    while s > 0:
        rx, ry = (x & s) > 0, (y & s) > 0
        d += s * s * ((3 * rx) ^ ry)
        if not ry:
            if rx:
                x, y = n - 1 - x, n - 1 - y
            x, y = y, x
        s //= 2
    return d


def quick_order(start, stops):
    """Anında (O(n log n)) makul sıra: Hilbert eğrisi, başlangıca en yakın noktadan başlayacak şekilde döndürülür."""
    n = len(stops)
    if n <= 2:
        return list(range(n))
    xs = [p[0] for p in stops]
    ys = [p[1] for p in stops]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0, 1.0)
    g = 1023
    key = [_hilbert_d(1024, int((p[0] - x0) / span * g), int((p[1] - y0) / span * g)) for p in stops]
    seq = sorted(range(n), key=key.__getitem__)
    near = min(range(n), key=lambda i: (stops[seq[i]][0] - start[0]) ** 2 + (stops[seq[i]][1] - start[1]) ** 2)
    # eğri açık uçlu; başlangıca yakın noktadan iki yönden hangisi daha kısaysa onu seç
    fwd = seq[near:] + seq[:near]
    back = list(reversed(seq[:near + 1])) + list(reversed(seq[near + 1:]))
    return fwd if route_length(start, stops, fwd) <= route_length(start, stops, back) else back


class MapGuide:
    def __init__(self, cubes=None, landmarks=None, store=None, prior=None):
        self.cubes = cubes if cubes is not None else _load("altgard_cubes.json")
        self.landmarks = landmarks if landmarks is not None else _load("landmarks.json")
        self.store = Path(store) if store else store_path()
        self.anchors: list[dict] = []
        self.prior = prior
        self.default_tf = False
        self.tf = None
        self.visited: set[int] = set()
        self._game: list[tuple[float, float]] = []
        self._stops: list[tuple[int, list[int]]] = []   # (merkez, kapsananlar)
        self._plan_key = None
        self._ver = 0
        self.off: set[int] = set()            # kara dışı (şüpheli) noktalar
        self._order_ids: list[int] = []
        self._order_pos = None
        self._pending = None
        self._worker = None
        self.sync_order = False               # testler için: sıralamayı aynı iş parçacığında yap
        self.reorder_budget = 1.5
        self._load_store()

    # --- kalıcılık ---
    def _load_store(self):
        try:
            self.anchors = list(json.loads(self.store.read_text("utf-8")).get("anchors", []))
        except (OSError, ValueError):
            self.anchors = []
        self.solve()

    def _save(self):
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps({"anchors": self.anchors}), "utf-8")
        except OSError:
            pass

    # --- kalibrasyon ---
    def landmark(self, name):
        return next((l for l in self.landmarks if l[0] == name), None)

    def add_anchor(self, name, gx, gy):
        lm = self.landmark(name)
        if lm is None:
            raise ValueError("Bu yerin harita koordinatı kayıtlı değil: 'Rota haritasında işaretle…' ile haritada yerine tıkla.")
        self.anchors = [a for a in self.anchors if a["name"] != name]
        self.anchors.append({"name": name, "px": lm[1], "py": lm[2], "gx": gx, "gy": gy})
        self._save()
        return self.solve()

    def add_anchor_px(self, name, px, py, gx, gy):
        """Haritada tıklanan piksel (px, py) + oyuncunun oyun konumu (gx, gy)."""
        if not name.strip():
            raise ValueError("Yer adı boş.")
        if not (0 <= px <= 4096 and 0 <= py <= 4096):
            raise ValueError("Harita dışı.")
        self.anchors = [a for a in self.anchors if a["name"] != name]
        self.anchors.append({"name": name.strip(), "px": px, "py": py, "gx": gx, "gy": gy})
        self._save()
        return self.solve()

    def anchor_errors(self):
        """{ad: hata_m}: her yerin dönüşüme göre sapması (yanlış tıklamayı yakalamak için)."""
        if not self.tf:
            return {}
        return {a["name"]: math.dist(self.to_game(a["px"], a["py"]), (a["gx"], a["gy"])) / 100 for a in self.anchors}

    def remove_last(self):
        if self.anchors:
            self.anchors.pop()
            self._save()
            self.solve()

    def clear(self):
        self.anchors = []
        self.visited.clear()
        self._save()
        self.solve()

    def spread(self):
        pts = [(a["px"], a["py"]) for a in self.anchors]
        return max((math.dist(p, q) for p in pts for q in pts), default=0.0)

    def _regularize(self, tf):
        """Serbest uyum önsel dönüşüme çok yakınsa (ölçek %3, dönme 2° içinde, yansıma yok), ölçek/dönmeyi
        önsele sabitleyip yalnızca ofseti uydurur: birkaç tıklamanın gürültüsü ölçeği bozmaz."""
        a, b, flip, _ = tf
        pa = self.prior[0]
        if flip or abs(a / pa - 1) >= 0.03 or abs(math.atan2((a / pa).imag, (a / pa).real)) >= math.radians(2):
            return tf
        q = [complex(x["px"], x["py"]) for x in self.anchors]
        g = [complex(x["gx"], x["gy"]) for x in self.anchors]
        b2 = sum(gi - pa * qi for gi, qi in zip(g, q)) / len(q)
        rms = math.sqrt(sum(abs(pa * qi + b2 - gi) ** 2 for qi, gi in zip(q, g)) / len(q))
        return (pa, b2, False, rms)

    def solve(self):
        """Anchor'lardan dönüşümü hesaplar. Yeterli anchor yoksa ve önsel verildiyse önsel kullanılır
        (default_tf=True, dönüş değeri None)."""
        self.tf, self._game, self._plan_key, self._order_ids, self._stops = None, [], None, [], []
        self._ver += 1
        self._order_pos = None
        self.default_tf = False
        fit_ok = len(self.anchors) >= 2 and self.spread() >= MIN_SPREAD_PX
        if fit_ok:
            tf = fit([(a["px"], a["py"], a["gx"], a["gy"]) for a in self.anchors])
            self.tf = self._regularize(tf) if (tf and self.prior) else tf
        elif self.prior:
            self.tf, self.default_tf = (*self.prior, 0.0), True
        if self.tf:
            self._game = [self.to_game(px, py) for px, py in self.cubes]
        return self.tf if fit_ok else None

    @property
    def ready(self):
        return self.tf is not None

    @property
    def rms_m(self):
        return self.tf[3] / 100 if self.tf else None

    @property
    def approximate(self):
        return self.default_tf or len(self.anchors) < 3

    def to_game(self, px, py):
        a, b, flip, _ = self.tf
        z = a * complex(px, -py if flip else py) + b
        return z.real, z.imag

    def to_map(self, gx, gy):
        """Oyun koordinatı -> harita pikseli (çizim için)."""
        a, b, flip, _ = self.tf
        q = (complex(gx, gy) - b) / a
        return q.real, (-q.imag if flip else q.imag)

    def view_coeffs(self, px, py, s, cx, cy, k):
        """PIL AFFINE katsayıları: çıktı pikseli (u, v) -> harita görseli pikseli.

        Çıktı, oyuncu (px, py) merkezde, kuzey = yukarı (oyun çerçevesi), s piksel/cm ölçekli bir görünümdür;
        (cx, cy) merkezin çıktıdaki konumu, k = görsel_piksel / harita_birimi. Dönüşümün dönme/yansıması dahildir.
        """
        a, b, flip, _ = self.tf
        inv = 1 / a
        g0 = complex(px - b.real, py - b.imag)
        sg = -1 if flip else 1
        iu, iv = inv / s, inv * 1j / s
        c0 = inv * g0 - iu * cx - iv * cy
        return (k * iu.real, k * iv.real, k * c0.real,
                k * sg * iu.imag, k * sg * iv.imag, k * sg * c0.imag)

    @property
    def cm_per_px(self):
        return abs(self.tf[0]) if self.tf else None

    # --- rota ---
    def reset_visits(self):
        self.visited.clear()
        self._ver += 1

    def update_visited(self, pos, radius_cm: float, fresh: bool):
        """Taze konumda R içine girilip gerçek küp paketi yoksa noktalar elenir."""
        if not (self.ready and pos and fresh):
            return
        r2 = radius_cm ** 2
        for i, (x, y) in enumerate(self._game):
            if i not in self.visited and (x - pos[0]) ** 2 + (y - pos[1]) ** 2 <= r2:
                self.visited.add(i)

    def _alive(self, live_cubes):
        live = [(c.x, c.y) for c in live_cubes]
        return [i for i, (x, y) in enumerate(self._game)
                if i not in self.visited and i not in self.off and not any((x - lx) ** 2 + (y - ly) ** 2 <= LIVE_MATCH_CM ** 2 for lx, ly in live)]

    def apply_land_filter(self, inside):
        """inside(mx, my) -> bool. Kara dışındaki noktalar rotadan ve adaylardan çıkarılır (None: filtre yok)."""
        self.off = set() if inside is None else {i for i, (mx, my) in enumerate(self.cubes) if not inside(mx, my)}
        self._ver += 1

    def plan(self, pos, radius_cm: float, live_cubes=()):
        """Kalan duraklar sırayla: [(x, y, kapsanan_sayısı)].

        Örtü yalnızca yarıçap/kalibrasyon/filtre/sıfırlamada yeniden hesaplanır; ziyaretlerde sadece
        tamamen boşalan duraklar düşer ve sıra korunur. Oyuncu 2R'den fazla uzaklaşınca sıra arka plan
        iş parçacığında yeniden optimize edilir (arayüz donmaz); o sırada eski sıra kullanılır.
        """
        if not (self.ready and pos):
            return []
        key = (round(radius_cm), self._ver)
        if key != self._plan_key:
            self._stops = cover(self._game, radius_cm, self._alive(()))
            self._plan_key = key
            self._order_ids, self._order_pos, self._pending = [], None, None
        keep = [(c, [j for j in got if j not in self.visited]) for c, got in self._stops]
        keep = [(c, g) for c, g in keep if g]
        self._stops = keep
        by_id = dict(keep)
        if self._pending is not None:
            ids, p0 = self._pending
            self._pending, self._order_pos = None, p0
            self._order_ids = [i for i in ids if i in by_id]
        cur = [i for i in self._order_ids if i in by_id]
        seen = set(cur)
        extra = [i for i in by_id if i not in seen]
        if extra:   # ilk durum: Hilbert eğrisiyle anında makul sıra; optimize edilmişi arka planda gelir
            if not cur:
                qo = quick_order((pos[0], pos[1]), [self._game[i] for i in extra])
                cur = [extra[j] for j in qo]
            else:
                extra.sort(key=lambda i: (self._game[i][0] - pos[0]) ** 2 + (self._game[i][1] - pos[1]) ** 2)
                cur += extra
        self._order_ids = cur
        drift = self._order_pos is None or math.dist(self._order_pos, pos[:2]) > 2 * radius_cm
        if drift and len(cur) > 1 and (self._worker is None or not self._worker.is_alive()):
            self._order_pos = (pos[0], pos[1])
            start, ids = (pos[0], pos[1]), list(cur)
            centers = [self._game[i] for i in ids]

            def work():
                self._pending = ([ids[j] for j in order(start, centers, budget=self.reorder_budget)], start)

            if self.sync_order:
                work()
                self._pending, (ids2, p0) = None, self._pending
                self._order_ids = [i for i in ids2 if i in by_id]
                cur = self._order_ids
            else:
                self._worker = threading.Thread(target=work, daemon=True, name="route-order")
                self._worker.start()
        live = [(c.x, c.y) for c in live_cubes]
        if live:   # canlı küpün zaten görüldüğü duraklar listeden gizlenir (kalıcı elenmez)
            shown = [i for i in cur if not any(
                (self._game[i][0] - lx) ** 2 + (self._game[i][1] - ly) ** 2 <= LIVE_MATCH_CM ** 2 for lx, ly in live)]
        else:
            shown = cur
        return [(*self._game[i], len(by_id[i])) for i in shown]

    def summary(self, pos, radius_cm, live_cubes=()):
        """(durak_sayısı, kalan_aday, toplam_yol_m) veya None."""
        route = self.plan(pos, radius_cm, live_cubes)
        if not route:
            return None
        pts = [(pos[0], pos[1])] + [(x, y) for x, y, _ in route]
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) / 100
        return len(route), sum(n for _, _, n in route), length

    def next_stop(self, pos, radius_cm, live_cubes=()):
        """(yatay_mesafe_m, bearing_rad, durak_sayısı, kalan_aday, yol_m, (x, y)) veya None."""
        route = self.plan(pos, radius_cm, live_cubes)
        if not route:
            return None
        x, y, _ = route[0]
        s = self.summary(pos, radius_cm, live_cubes)
        return (math.hypot(x - pos[0], y - pos[1]) / 100, math.atan2(x - pos[0], -(y - pos[1])), s[0], s[1], s[2], (x, y))
