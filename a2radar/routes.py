"""Gezme rotaları: sıralama ve 'sıradaki durak' mantığı (oyuna girdi göndermez; yön ve mesafe önerir)."""
from __future__ import annotations

import math

REACHED_M = 15.0


def length_m(pts) -> float:
    return sum(math.hypot(a["x_m"] - b["x_m"], a["y_m"] - b["y_m"]) for a, b in zip(pts, pts[1:]))


def order_nearest(start, pts, improve: bool = True) -> list:
    """start=(x_m, y_m) başlangıcından en yakın komşu; ardından küçük 2-opt. pts: [{x_m, y_m, ...}]."""
    left = list(pts)
    cur, out = start, []
    while left:
        i = min(range(len(left)), key=lambda j: math.hypot(left[j]["x_m"] - cur[0], left[j]["y_m"] - cur[1]))
        p = left.pop(i)
        out.append(p)
        cur = (p["x_m"], p["y_m"])
    if improve and len(out) > 3:
        out = _two_opt(start, out)
    return out


def _two_opt(start, pts):
    def d(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    xy = [start] + [(p["x_m"], p["y_m"]) for p in pts]
    order = list(range(1, len(xy)))
    improved, rounds = True, 0
    while improved and rounds < 30:
        improved, rounds = False, rounds + 1
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                a = xy[order[i - 1]] if i else xy[0]
                b, c = xy[order[i]], xy[order[j]]
                nxt = xy[order[j + 1]] if j + 1 < len(order) else None
                old = d(a, b) + (d(c, nxt) if nxt else 0.0)
                new = d(a, c) + (d(b, nxt) if nxt else 0.0)
                if new + 1e-9 < old:
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    return [pts[k - 1] for k in order]


class Walker:
    """Rota üzerinde ilerleme: oyuncu durağa REACHED_M içine girince sıradakine geçer."""

    def __init__(self, pts=None, loop: bool = False):
        self.pts = list(pts or [])
        self.i = 0
        self.loop = loop

    def reset(self, pts=None):
        if pts is not None:
            self.pts = list(pts)
        self.i = 0

    @property
    def done(self) -> bool:
        return self.i >= len(self.pts)

    def target(self, pos_m):
        """(durak, yatay mesafe m, yön radyan 0=kuzey/oyun -y) ya da None. pos_m=(x, y) metre."""
        if not self.pts or pos_m is None:
            return None
        for _ in range(len(self.pts) + 1):      # sonsuz döngüye karşı üst sınır
            if self.done:
                return None
            p = self.pts[self.i]
            dx, dy = p["x_m"] - pos_m[0], p["y_m"] - pos_m[1]
            dist = math.hypot(dx, dy)
            if dist > REACHED_M:
                return p, dist, math.atan2(dx, -dy)
            self.i += 1
            if self.done and self.loop:
                self.i = 0
        return None
