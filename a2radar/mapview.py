"""Etkileşimli harita bileşeni: kaydır (sürükle), yakınlaştır (tekerlek), oyuncuyu takip et, işaretleyicileri çiz.

Katmanları `layers_fn()` verir (her yenilemede çağrılır):
  {"monsters": [(x_cm, y_cm, sayı, ad)], "resources": [...], "live": [Seen...], "route": [{x_m, y_m, label}],
   "route_i": aktif durak indeksi, "areas": [{name, x_m, y_m, radius_m}], "target": (x_m, y_m) | None}
Koordinatlar oyun dünyası (santimetre ya da metre, anahtarda belirtildiği gibi); harita pikseli yalnız çizimde kullanılır.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from . import mapcal

SPACE = 4096.0
NICE_M = (10, 25, 50, 100, 200, 500, 1000, 2000)
BG = "#0e0d14"
COL = {"canavar": "#ef4444", "kaynak": "#4ade80", "npc": "#60a5fa", "diğer": "#a1a1aa", "": "#a1a1aa"}


class MapView(ttk.Frame):
    def __init__(self, parent, hub, layers_fn, click_cb=None, rclick_cb=None, dim: float = 0.35, scale: float = 1.4):
        super().__init__(parent)
        self.hub, self.layers_fn, self.click_cb, self.rclick_cb, self.dim = hub, layers_fn, click_cb, rclick_cb, dim
        self.cx, self.cy, self.scale = SPACE / 2, SPACE / 2, scale
        self.follow = True
        self.photo = None
        self._key = None
        self.markers: list[tuple] = []
        self._press = None
        self._cursor = ""
        self.c = tk.Canvas(self, bg=BG, highlightthickness=0, width=520, height=420)
        self.c.pack(fill="both", expand=True)
        self.info = ttk.Label(self, text="", foreground="#666", font=("Consolas", 9))
        self.info.pack(fill="x")
        c = self.c
        c.bind("<ButtonPress-1>", self._down)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._up)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Motion>", self._hover)
        c.bind("<Button-3>", self._right)
        c.bind("<Configure>", lambda _e: self.redraw())
        self.after(500, self._tick)

    # --- dönüşümler ---
    def _tf(self):
        g = self.hub.guide
        if g is not None and g.ready:
            return g.to_map, g.to_game, g.cm_per_px
        a, b = mapcal.PRIOR[0], mapcal.PRIOR[1]
        return (lambda gx, gy: (((complex(gx, gy) - b) / a).real, ((complex(gx, gy) - b) / a).imag),
                lambda px, py: ((a * complex(px, py) + b).real, (a * complex(px, py) + b).imag), abs(a))

    def to_px(self, gx_cm, gy_cm):
        to_map, _, _ = self._tf()
        mx, my = to_map(gx_cm, gy_cm)
        return ((mx - self.cx) * self.scale + self.c.winfo_width() / 2, (my - self.cy) * self.scale + self.c.winfo_height() / 2)

    def to_game_m(self, ex, ey):
        _, to_game, _ = self._tf()
        mx, my = (ex - self.c.winfo_width() / 2) / self.scale + self.cx, (ey - self.c.winfo_height() / 2) / self.scale + self.cy
        gx, gy = to_game(mx, my)
        return gx / 100, gy / 100

    def center_on_player(self):
        self.follow = True
        self.redraw()

    # --- etkileşim ---
    def _down(self, e):
        self._press = (e.x, e.y, self.cx, self.cy, False)

    def _drag(self, e):
        if not self._press:
            return
        x0, y0, cx0, cy0, _ = self._press
        if abs(e.x - x0) + abs(e.y - y0) > 4 or self._press[4]:
            self._press = (x0, y0, cx0, cy0, True)
            self.follow = False
            self.cx, self.cy = cx0 - (e.x - x0) / self.scale, cy0 - (e.y - y0) / self.scale
            self.redraw()

    def _up(self, e):
        p, self._press = self._press, None
        if p and not p[4] and self.click_cb:
            gx, gy = self.to_game_m(e.x, e.y)
            self.click_cb(gx, gy, e.x, e.y, self)

    def _right(self, e):
        if self.rclick_cb:
            gx, gy = self.to_game_m(e.x, e.y)
            self.rclick_cb(gx, gy, e.x, e.y, self)

    def _wheel(self, e):
        k = 1.25 if e.delta > 0 else 0.8
        new = min(10.0, max(0.12, self.scale * k))
        if not self.follow:       # imlecin altındaki nokta sabit kalsın
            w, h = self.c.winfo_width(), self.c.winfo_height()
            mx, my = (e.x - w / 2) / self.scale + self.cx, (e.y - h / 2) / self.scale + self.cy
            self.cx, self.cy = mx - (e.x - w / 2) / new, my - (e.y - h / 2) / new
        self.scale = new
        self.redraw()

    def _hover(self, e):
        gx, gy = self.to_game_m(e.x, e.y)
        txt = f"X {gx:.0f}  Y {gy:.0f} m"
        best, bd = None, 10.0
        for sx, sy, t in self.markers:
            d = math.hypot(sx - e.x, sy - e.y)
            if d < bd:
                best, bd = t, d
        self._cursor = txt + (f"   ·   {best}" if best else "")
        self.info.config(text=self._cursor)

    # --- çizim ---
    def _tick(self):
        if self.winfo_exists():
            if self.winfo_viewable():
                self.redraw()
            self.after(500, self._tick)

    def _background(self, w, h):
        img = self.hub.mapimg
        if img is None:
            return None
        key = (round(self.cx, 1), round(self.cy, 1), round(self.scale, 3), w, h)
        if key != self._key:
            from PIL import Image, ImageTk
            im = img.crop(self.cx, self.cy, w / 2 / self.scale, h / 2 / self.scale, w, h).convert("RGB")
            if self.dim:
                im = Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), self.dim)
            self.photo = ImageTk.PhotoImage(im)
            self._key = key
        return self.photo

    def redraw(self):
        c = self.c
        w, h = c.winfo_width(), c.winfo_height()
        if w < 20 or h < 20:
            return
        snap = self.hub.state.snapshot()
        to_map, _, cm_px = self._tf()
        if self.follow and snap.pos:
            self.cx, self.cy = to_map(snap.pos[0], snap.pos[1])
        c.delete("all")
        bg = self._background(w, h)
        if bg is not None:
            c.create_image(0, 0, anchor="nw", image=bg)
        else:
            for i in range(0, w, 80):
                c.create_line(i, 0, i, h, fill="#1a1826")
            for j in range(0, h, 80):
                c.create_line(0, j, w, j, fill="#1a1826")
        L = self.layers_fn() or {}
        self.markers = []
        px = self.to_px
        z = self.scale

        for a in L.get("areas", ()):                                  # farm alanları
            x, y = px(a["x_m"] * 100, a["y_m"] * 100)
            r = a["radius_m"] * 100 / cm_px * z
            c.create_oval(x - r, y - r, x + r, y + r, outline="#a78bfa", width=2, dash=(5, 3))
            c.create_text(x, y - r - 8, text=a.get("name", ""), fill="#c4b5fd", font=("Segoe UI", 8, "bold"))

        for key, shape, fill in (("monsters", "o", "#f97316"), ("resources", "d", "#22c55e")):
            pts = L.get(key, ())
            label = z >= 1.2 and len(pts) <= 60
            for x_cm, y_cm, n, name in pts:
                x, y = px(x_cm, y_cm)
                if not (-10 < x < w + 10 and -10 < y < h + 10):
                    continue
                r = 5 if n > 1 else 4
                if shape == "o":
                    c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline="#fff")
                else:
                    c.create_polygon(x, y - r - 1, x + r + 1, y, x, y + r + 1, x - r - 1, y, fill=fill, outline="#fff")
                if label:
                    c.create_text(x + 8, y, anchor="w", text=name, fill="#fff", font=("Segoe UI", 8))
                self.markers.append((x, y, f"{name} · {n}× görüldü"))

        if self.hub.show_live.get():                                  # canlı varlıklar
            for s in L.get("live", ()):
                x, y = px(s.x_m * 100, s.y_m * 100)
                if -10 < x < w + 10 and -10 < y < h + 10:
                    col = COL.get(s.kind, "#a1a1aa")
                    c.create_oval(x - 3, y - 3, x + 3, y + 3, outline=col, width=2)
                    if z >= 2:
                        c.create_text(x + 6, y, anchor="w", text=s.name, fill=col, font=("Segoe UI", 8))
                    self.markers.append((x, y, f"{s.name} · {s.dist_m:.0f} m {s.compass}" if s.dist_m is not None else s.name))

        route = L.get("route") or []
        if route:
            pts = [px(p["x_m"] * 100, p["y_m"] * 100) for p in route]
            if snap.pos and L.get("route_from_player"):
                pts_line = [px(snap.pos[0], snap.pos[1])] + pts[L.get("route_i", 0):]
            else:
                pts_line = pts
            if len(pts_line) > 1:
                c.create_line(*[v for p in pts_line for v in p], fill="#38bdf8", width=2)
            for i, (x, y) in enumerate(pts):
                cur = i == L.get("route_i", -1)
                c.create_oval(x - 8, y - 8, x + 8, y + 8, fill="#0b3b52" if not cur else "#854d0e",
                              outline="#fde047" if cur else "#7dd3fc", width=2)
                c.create_text(x, y, text=str(i + 1), fill="#fff", font=("Segoe UI", 8, "bold"))
                self.markers.append((x, y, f"Durak {i + 1} {route[i].get('label', '')}".strip()))

        if L.get("target") and snap.pos:                              # asistanın önerdiği hedef
            tx, ty = px(L["target"][0] * 100, L["target"][1] * 100)
            sx, sy = px(snap.pos[0], snap.pos[1])
            c.create_line(sx, sy, tx, ty, fill="#fde047", width=2, dash=(6, 3))
            c.create_oval(tx - 9, ty - 9, tx + 9, ty + 9, outline="#fde047", width=3)

        trail = self.hub.trail
        if len(trail) > 1:
            c.create_line(*[v for p in trail for v in px(*p)], fill="#e5e7eb", width=1, dash=(2, 3))

        if snap.pos:
            x, y = px(snap.pos[0], snap.pos[1])
            if snap.heading is not None:
                x1, y1 = px(snap.pos[0] + math.sin(snap.heading) * 100, snap.pos[1] - math.cos(snap.heading) * 100)
                d = math.hypot(x1 - x, y1 - y) or 1.0
                ux, uy = (x1 - x) / d, (y1 - y) / d
                c.create_polygon(x + ux * 14, y + uy * 14, x - ux * 6 - uy * 7, y - uy * 6 + ux * 7,
                                 x - ux * 6 + uy * 7, y - uy * 6 - ux * 7, fill="#facc15", outline="#fff")
            c.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#fff", outline="#22c55e", width=2)
            age = snap.pos_age
            head = f"Oyuncu  X {snap.pos[0] / 100:.0f}  Y {snap.pos[1] / 100:.0f}  Z {snap.pos[2] / 100:.0f} m"
            head += f"   ·   konum {age:.0f} sn eski" if age is not None and age > 3 else ""
        else:
            head = "Konum bekleniyor"
        c.create_text(8, 8, anchor="nw", text=head, fill="#e5e7eb", font=("Segoe UI", 9, "bold"))
        if not self.follow:
            c.create_text(w - 8, 8, anchor="ne", text="Takip kapalı (Beni ortala)", fill="#fbbf24", font=("Segoe UI", 8))

        want_px = 100.0                                               # ölçek çubuğu
        m_per_px = cm_px / 100 / z
        nice = min(NICE_M, key=lambda m: abs(m / m_per_px - want_px))
        bar = nice / m_per_px
        c.create_line(10, h - 14, 10 + bar, h - 14, fill="#fff", width=3)
        c.create_text(10 + bar / 2, h - 24, text=f"{nice} m", fill="#fff", font=("Segoe UI", 8))
        self.info.config(text=self._cursor or "Sürükle: kaydır · Tekerlek: yakınlaştır")
