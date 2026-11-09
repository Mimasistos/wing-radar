"""Radarın altındaki yarı saydam yol haritası: oyuncu merkezde, çevre noktalar, rota ve duraklar canlı.

Arka plan Windows 'transparentcolor' ile tamamen şeffaf; üstüne stipple ile %50 koyu bir panel çizilir.
Harita kuzey = yukarı. Fare tekerleği yakınlaştırır. Çizim saniyede ~5 kez yenilenir.
"""
from __future__ import annotations

import math
import tkinter as tk

BG_KEY = "#010101"
DIM = "#8a86a0"
W, MH = 260, 210
ZOOM_MIN, ZOOM_MAX = 100.0, 2000.0
NICE_M = (25, 50, 100, 200, 500, 1000)


class MiniMap:
    def __init__(self, ov):
        self.ov = ov
        self.canvas = tk.Canvas(ov.root, bg=BG_KEY, highlightthickness=0)
        self.shown = False
        for ev, fn in (("<ButtonPress-1>", ov._press_drag), ("<B1-Motion>", ov._drag), ("<ButtonRelease-1>", ov._release)):
            self.canvas.bind(ev, fn)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.photo = None   # PhotoImage referansı (GC'ye karşı)
        ov.root.attributes("-transparentcolor", BG_KEY)
        self.apply_scale()
        self.tick()

    def apply_scale(self):
        k = self.ov.k
        self.canvas.config(width=int(W * k), height=int(MH * k))

    def wanted(self) -> bool:
        ov = self.ov
        return bool(ov.cfg["minimap_on"] and ov.cfg["route_on"] and ov.guide and ov.guide.ready)

    def _wheel(self, e):
        cfg = self.ov.cfg
        f = 1 / 1.25 if e.delta > 0 else 1.25
        cfg["map_m"] = round(max(ZOOM_MIN, min(ZOOM_MAX, cfg["map_m"] * f)))

    def tick(self):
        want = self.wanted()
        if want and not self.shown:
            self.canvas.pack()
            self.shown = True
        elif not want and self.shown:
            self.canvas.pack_forget()
            self.shown = False
        if self.shown:
            self.draw()
        self.ov.root.after(200, self.tick)

    # --- çizim ---
    def draw(self):
        ov, c, g = self.ov, self.canvas, self.ov.guide
        k = ov.k
        w, h = int(W * k), int(MH * k)
        c.delete("all")
        c.create_rectangle(0, 0, w - 1, h - 1, fill="#000000", stipple="gray50", outline="#3a3560")
        font = lambda n, b="": ("Segoe UI", max(6, int(n * k)), b)
        c.create_text(6 * k, 9 * k, anchor="w", fill=DIM, font=font(7, "bold"), text="YOL HARİTASI · kuzey ↑")
        snap = ov.s.snapshot()
        if not snap.pos:
            c.create_text(w / 2, h / 2, fill=DIM, font=font(9), text="Konum bekleniyor (zıpla/dash yap)")
            return
        px, py = snap.pos[0], snap.pos[1]
        view_cm = ov.cfg["map_m"] * 100
        top = 16 * k
        cx, cy = w / 2, top + (h - top) / 2
        s = ((h - top) / 2 - 4 * k) / view_cm            # piksel / cm

        mi = ov.mapimg
        if mi and g.cm_per_px:
            try:
                from PIL import ImageTk
                iw, ih = w - 2, int(h - top - 1)
                coeffs = g.view_coeffs(px, py, s, cx, cy - top, mi.n / 4096.0)
                self.photo = ImageTk.PhotoImage(mi.warp(coeffs, iw, ih, alpha=150))
                c.create_image(1, top, anchor="nw", image=self.photo)
            except Exception:
                self.photo = None

        def to_scr(x, y):
            return cx + (x - px) * s, cy + (y - py) * s

        def inside(x, y, pad=0):
            return pad <= x <= w - pad and top + pad <= y <= h - pad

        # olası noktalar
        for i, (gx, gy) in enumerate(g._game):
            if i in g.off:
                continue
            x, y = to_scr(gx, gy)
            if inside(x, y, 2):
                done = i in g.visited
                c.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill="#4a4a55" if done else "#7aa2d6", outline="")

        rcm = ov.cfg["scan_m"] * 100
        route = g.plan(snap.pos, rcm, snap.cubes)
        rpx = rcm * s
        # tarama yarıçapı (oyuncu etrafında)
        c.create_oval(cx - rpx, cy - rpx, cx + rpx, cy + rpx, outline="#3f7a4a", dash=(3, 3))

        if route:
            pts = [(cx, cy)] + [to_scr(x, y) for x, y, _ in route[:14]]
            for j in range(len(pts) - 1):
                c.create_line(*pts[j], *pts[j + 1], fill="#38bdf8" if j == 0 else "#2b7ca3", width=max(1, int((3 if j == 0 else 2) * k)))
            for n, (x, y) in enumerate(pts[1:9], 1):
                if inside(x, y):
                    c.create_oval(x - rpx, y - rpx, x + rpx, y + rpx, outline="#235a7a")
                    c.create_text(x, y, text=str(n), fill="#cde8f7", font=font(7, "bold"))
            nx, ny = pts[1]
            if inside(nx, ny, 3 * k):
                c.create_oval(nx - 7 * k, ny - 7 * k, nx + 7 * k, ny + 7 * k, outline="#fde047", width=max(1, int(2 * k)))
            else:   # ekran dışı: kenarda yön işareti + mesafe
                dx, dy = nx - cx, ny - cy
                t = min((w / 2 - 10 * k) / abs(dx) if dx else 1e9, ((h - top) / 2 - 10 * k) / abs(dy) if dy else 1e9)
                ex, ey = cx + dx * t, cy + dy * t
                a = math.atan2(dx, -dy)
                tri = [(ex + 8 * k * math.sin(a), ey - 8 * k * math.cos(a)),
                       (ex + 6 * k * math.sin(a + 2.5), ey - 6 * k * math.cos(a + 2.5)),
                       (ex + 6 * k * math.sin(a - 2.5), ey - 6 * k * math.cos(a - 2.5))]
                c.create_polygon([v for p in tri for v in p], fill="#fde047", outline="")
                dist = math.hypot(route[0][0] - px, route[0][1] - py) / 100
                c.create_text(ex, ey + (12 * k if dy < 0 else -12 * k), fill="#fde047", font=font(8, "bold"), text=f"{dist:.0f} m")

        for cube in snap.cubes:
            x, y = to_scr(cube.x, cube.y)
            if inside(x, y, 4):
                c.create_oval(x - 6 * k, y - 6 * k, x + 6 * k, y + 6 * k, fill="#ef4444", outline="#ffffff")

        # oyuncu
        fresh = snap.heading is not None and snap.heading_age is not None and snap.heading_age < 3
        if fresh:
            a, r0 = snap.heading, 8 * k
            tri = [(cx + r0 * math.sin(a), cy - r0 * math.cos(a)),
                   (cx + 6 * k * math.sin(a + 2.5), cy - 6 * k * math.cos(a + 2.5)),
                   (cx + 6 * k * math.sin(a - 2.5), cy - 6 * k * math.cos(a - 2.5))]
            c.create_polygon([v for p in tri for v in p], fill="#ffffff", outline="#22c55e")
        else:
            c.create_oval(cx - 4 * k, cy - 4 * k, cx + 4 * k, cy + 4 * k, fill="#ffffff", outline="#22c55e", width=2)

        # ölçek çubuğu + özet
        nice = max((n for n in NICE_M if n <= ov.cfg["map_m"] * 0.6), default=NICE_M[0])
        bx, by, bl = 8 * k, h - 8 * k, nice * 100 * s
        c.create_line(bx, by, bx + bl, by, fill="#cfcfe0", width=2)
        c.create_text(bx + bl + 4 * k, by, anchor="w", fill="#cfcfe0", font=font(7), text=f"{nice} m")
        sm = g.summary(snap.pos, rcm, snap.cubes)
        txt = "Rota bitti" if sm is None else f"{sm[0]} durak · {sm[1]} aday"
        c.create_text(w - 6 * k, 9 * k, anchor="e", fill="#cde8f7", font=font(7, "bold"), text=txt)
        age = snap.pos_age
        if age is not None and age > 5:
            c.create_text(w / 2, h - 18 * k, fill="#f59e0b", font=font(7, "bold"), text=f"konum {age:.0f} sn eski: zıpla/dash yap")
