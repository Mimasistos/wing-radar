from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

from . import audio, entities, mapcal, mapimg, minimap, settings
from .entwin import EntitiesWindow
from .capture import Capture
from .state import State, nearest

BG, FG, DIM = "#14121c", "#e8e6f0", "#8a86a0"
W, H = 260, 250


def zone_color(d: float) -> str:
    return "#ef4444" if d > 50 else "#f59e0b" if d > 15 else "#22c55e"


class Overlay:
    def __init__(self, root: tk.Tk, state: State, cfg: dict):
        self.root, self.s, self.cfg = root, state, cfg
        self.error = ""
        self.msg, self.msg_until, self.msg_ok = "", 0.0, False
        self.sweep = 0.0
        self.first_seen: list[float] = []   # küpün ilk görüldüğü yatay mesafeler (m)
        try:
            self.guide = mapcal.MapGuide(prior=mapcal.PRIOR)
        except (OSError, ValueError):
            self.guide = None
        self.mapimg = mapimg.load()          # isteğe bağlı arka plan; yoksa None
        self.labels = entities.Labels()      # kullanıcının canavar/NPC kimlik etiketleri
        self._inside = None
        self.apply_land_filter()
        root.title("A2 Radar")
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", cfg["alpha"])
        root.configure(bg=BG)
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack()
        self._apply_scale()
        root.geometry(f"+{cfg['x']}+{cfg['y']}")
        self.c.bind("<ButtonPress-1>", self._press)
        self.c.bind("<B1-Motion>", self._drag)
        self.c.bind("<ButtonRelease-1>", self._release)
        state.on_new_cube = self._on_cube
        self.mm = minimap.MiniMap(self)
        self.tick()

    def apply_land_filter(self):
        """Kara dışına düşen (şüpheli) olası noktaları rotadan çıkarır; harita görseli gerekir."""
        if not self.guide:
            return
        if self.cfg["land_only"] and self.mapimg:
            if self._inside is None:
                self._inside = self.mapimg.land_inside()
            self.guide.apply_land_filter(self._inside)
        else:
            self.guide.apply_land_filter(None)

    def _on_cube(self, cube):
        if self.cfg["sound"]:
            audio.play(self.cfg["profile"], self.cfg["volume"])
        snap = self.s.snapshot()
        if snap.pos and snap.pos_age is not None and snap.pos_age < 3:
            self.first_seen = (self.first_seen + [math.hypot(cube.x - snap.pos[0], cube.y - snap.pos[1]) / 100])[-20:]

    def _apply_scale(self):
        k = self.cfg["scale"]
        self.k = k
        self.c.config(width=int(W * k), height=int(H * k))
        if getattr(self, "mm", None):
            self.mm.apply_scale()

    # --- sürükle / ayarlar / kapat ---
    def _press_drag(self, e):
        self._off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._moved = False

    def _press(self, e):
        self._press_drag(e)
        k = self.k
        if e.x > (W - 28) * k and e.y < 26 * k:
            self._close()
        elif e.x > (W - 54) * k and e.y < 26 * k:
            self._off = None
            SettingsDialog(self)

    def _drag(self, e):
        if getattr(self, "_off", None):
            self._moved = True
            self.root.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")

    def _release(self, _):
        if getattr(self, "_moved", False):
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()

    def _close(self):
        settings.save(self.cfg)
        self.root.destroy()

    # --- çizim ---
    def tick(self):
        if self.s.calib_until and not self.s.calibrating:
            self._calib_done()
        if not self.root.winfo_viewable():       # gizliyken (Hub içinde) çizme: yakalama iş parçacığına CPU kalsın
            self.root.after(250, self.tick)
            return
        snap = self.s.snapshot()
        c, k = self.c, self.k
        c.delete("all")
        c.create_text(10 * k, 13 * k, anchor="w", fill=DIM, font=("Segoe UI", int(9 * k), "bold"), text="A2 RADAR")
        c.create_text((W - 40) * k, 13 * k, fill=DIM, font=("Segoe UI", int(12 * k)), text="⚙")
        c.create_text((W - 14) * k, 13 * k, fill=DIM, font=("Segoe UI", int(11 * k)), text="✕")

        if self.s.calibrating:
            base = self.s.calib_phase == "base"
            left = (self.s.calib_base_until if base else self.s.calib_until) - time.time()
            c.create_rectangle(0, 24 * k, W * k, 46 * k, fill="#38bdf8" if base else "#f59e0b", outline="")
            c.create_text(W / 2 * k, 35 * k, fill="#000", font=("Segoe UI", int(10 * k), "bold"),
                          text=(f"DUR, HAREKET ETME  {left:.0f} sn" if base else f"ZIPLA / DASH / KANAT!  {left:.0f} sn"))
        elif time.time() < self.msg_until:
            h = 22 if self.msg_ok else 64                  # hata mesajları uzun: bant yüksek, metin sarılı
            c.create_rectangle(0, 24 * k, W * k, (24 + h) * k, fill="#16a34a" if self.msg_ok else "#b91c1c", outline="")
            c.create_text(W / 2 * k, (24 + h / 2) * k, fill="#fff", font=("Segoe UI", int(8 * k), "bold"),
                          text=self.msg, width=(W - 12) * k)

        cx, cy, r = W / 2 * k, 110 * k, 62 * k
        hit = nearest(snap, self.cfg["range_m"])
        if hit:
            cube, dist, dz, bearing = hit
            col = zone_color(dist)
            # Yön biliniyorsa yaya göre göreli, değilse kuzey = yukarı
            fresh = snap.heading is not None and snap.heading_age is not None and snap.heading_age < 3
            rel = bearing - snap.heading if fresh else bearing
            self._arrow(cx, cy, r, rel, col)
            c.create_text(cx, 190 * k, fill=col, font=("Segoe UI", int(22 * k), "bold"), text=f"{dist:.0f} m")
            if abs(dz) < 2:
                v = "═ AYNI SEVİYE"
            else:
                v = f"{'↑ YUKARIDA' if dz > 0 else '↓ AŞAĞIDA'} {abs(dz):.0f} m"
            c.create_text(cx, 212 * k, fill=FG, font=("Segoe UI", int(10 * k)), text=v)
            extra = "" if fresh else "ok: kuzey = yukarı (koşunca yön güncellenir)"
            age = snap.pos_age
            if age is not None and age > 5:
                extra = f"konum {age:.0f} sn eski: zıpla/dash yap"
            c.create_text(cx, 232 * k, fill=DIM, font=("Segoe UI", int(8 * k)), text=extra)
        elif self._route_draw(snap, cx, cy, r):
            pass
        else:
            self._sweeper(cx, cy, r)
            c.create_text(cx, 205 * k, fill=DIM, font=("Segoe UI", int(10 * k)), text=self._status(snap))
            c.create_text(cx, 228 * k, fill=DIM, font=("Segoe UI", int(8 * k)), text=self.error[:48])
        self.root.after(33, self.tick)

    def route_state(self, snap):
        """(guide|None, next_stop|None, R_cm). Taze konumda ziyaret işaretler."""
        g = self.guide
        if not (g and g.ready and self.cfg["route_on"] and snap.pos and snap.player_id is not None):
            return None, None, 0
        rcm = self.cfg["scan_m"] * 100
        age = snap.pos_age
        g.update_visited(snap.pos, rcm, fresh=age is not None and age < 3)
        return g, g.next_stop(snap.pos, rcm, snap.cubes), rcm

    def _route_draw(self, snap, cx, cy, r) -> bool:
        g, ns, _ = self.route_state(snap)
        if g is None:
            return False
        c, k = self.c, self.k
        if ns is None:
            self._sweeper(cx, cy, r)
            c.create_text(cx, 205 * k, fill=FG, font=("Segoe UI", int(10 * k), "bold"), text="Rota bitti: tüm noktalar tarandı")
            c.create_text(cx, 228 * k, fill=DIM, font=("Segoe UI", int(8 * k)), text="Rota haritasından ziyaretleri sıfırla")
            return True
        dist, bearing, stops, left, length, _ = ns
        fresh = snap.heading is not None and snap.heading_age is not None and snap.heading_age < 3
        self._arrow(cx, cy, r, bearing - snap.heading if fresh else bearing, "#38bdf8")
        c.create_text(cx, 190 * k, fill="#38bdf8", font=("Segoe UI", int(22 * k), "bold"), text=f"{dist:.0f} m")
        c.create_text(cx, 212 * k, fill=FG, font=("Segoe UI", int(9 * k)),
                      text=f"sonraki durak · {stops} durak · {left} aday · {length / 1000:.1f} km")
        age = snap.pos_age
        if age is not None and age > 5:
            note = f"konum {age:.0f} sn eski: zıpla/dash yap"
        elif g.approximate:
            note = "tahmini harita: 3. yer ekle"
        elif not fresh:
            note = "ok: kuzey = yukarı (koşunca yön güncellenir)"
        else:
            note = ""
        c.create_text(cx, 232 * k, fill=DIM, font=("Segoe UI", int(8 * k)), text=note)
        return True

    def _status(self, snap):
        if not snap.connected:
            return "Oyun bekleniyor…"
        if snap.player_id is None:
            return "⚙ → 'Karakterimi bul' (kimlik yok)"
        if snap.pos is None:
            return "Konum bekleniyor (zıpla/dash yap)"
        if self.guide and self.cfg["route_on"] and not self.guide.ready:
            return "⚙ → 'Harita kalibrasyonu' (3 yer)"
        return "Küp aranıyor"

    def _calib_done(self):
        eid, msg = self.s.finish_calibration()
        self.msg, self.msg_until, self.msg_ok = msg, time.time() + (8 if eid is not None else 14), eid is not None

    def _sweeper(self, cx, cy, r):
        c = self.c
        self.sweep = (self.sweep + 0.07) % (2 * math.pi)
        for f in (1, .66, .33):
            c.create_oval(cx - r * f, cy - r * f, cx + r * f, cy + r * f, outline="#2a2640")
        c.create_line(cx, cy, cx + r * math.sin(self.sweep), cy - r * math.cos(self.sweep), fill="#A855F7", width=2)

    def _arrow(self, cx, cy, r, ang, col):
        s, co = math.sin(ang), math.cos(ang)

        def pt(fwd, side):  # fwd: ileri, side: sağ
            return cx + fwd * s + side * co, cy - fwd * co + side * s

        k = self.k
        self.c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#2a2640", width=2)
        a = r * .85
        # gölge katmanı + ön yüz: hafif 3B hissi
        for dx, dy, fill in ((3 * k, 4 * k, "#0b0a10"), (0, 0, col)):
            poly = [pt(a, 0), pt(-a * .55, a * .6), pt(-a * .25, 0), pt(-a * .55, -a * .6)]
            self.c.create_polygon([v + (dx if i % 2 == 0 else dy) for p in poly for i, v in enumerate(p)],
                                  fill=fill, outline="")
        self.c.create_polygon(*pt(a, 0), *pt(-a * .25, 0), *pt(-a * .55, -a * .6), fill="#ffffff", stipple="gray25")


class SettingsDialog(tk.Toplevel):
    def __init__(self, ov: Overlay):
        super().__init__(ov.root)
        self.ov, cfg = ov, ov.cfg
        self.title("A2 Radar Ayarları")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        f = ttk.Frame(self, padding=12)
        f.pack()
        self.vars = {}
        rows = (("scale", "Boyut", .75, 1.5), ("alpha", "Saydamlık", .2, 1), ("volume", "Ses", 0, 1),
                ("range_m", "Menzil (m)", 20, 20000), ("scan_m", "Tarama yarıçapı (m)", 20, 500),
                ("map_m", "Harita yakınlığı (m)", 100, 2000))
        for i, (key, label, lo, hi) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w")
            v = tk.DoubleVar(value=cfg[key])
            self.vars[key] = v
            ttk.Scale(f, from_=lo, to=hi, variable=v, length=180,
                      command=lambda _=0, key=key: self._live(key)).grid(row=i, column=1, pady=3)
        r = len(rows)
        self.snd = tk.BooleanVar(value=cfg["sound"])
        ttk.Checkbutton(f, text="Sesli uyarı", variable=self.snd, command=self._sound).grid(row=r, columnspan=2, sticky="w")
        self.prof = tk.StringVar(value=cfg["profile"])
        cb = ttk.Combobox(f, values=list(audio.PROFILES), textvariable=self.prof, state="readonly", width=16)
        cb.grid(row=r + 1, column=0, columnspan=2, sticky="we", pady=3)
        cb.bind("<<ComboboxSelected>>", lambda _: (cfg.update(profile=self.prof.get()), audio.play(self.prof.get(), cfg["volume"])))
        ttk.Label(f, text="Karakter adı (isteğe bağlı)").grid(row=r + 2, columnspan=2, sticky="w")
        self.name = tk.StringVar(value=cfg["char_name"])
        e = ttk.Entry(f, textvariable=self.name)
        e.grid(row=r + 3, columnspan=2, sticky="we")
        e.bind("<KeyRelease>", lambda _: self._name())
        ttk.Button(f, text="Karakterimi bul (3 sn dur, 6 sn zıpla)", command=self._calib).grid(row=r + 4, columnspan=2, sticky="we", pady=3)
        self.route = tk.BooleanVar(value=cfg["route_on"])
        ttk.Checkbutton(f, text="Arama rotası (küp yokken sonraki durağa yönlendir)", variable=self.route,
                        command=lambda: cfg.update(route_on=self.route.get())).grid(row=r + 5, columnspan=2, sticky="w", pady=(6, 0))
        self.mmap = tk.BooleanVar(value=cfg["minimap_on"])
        ttk.Checkbutton(f, text="Radarın altında yol haritası paneli (tekerlek: yakınlaştır)", variable=self.mmap,
                        command=lambda: cfg.update(minimap_on=self.mmap.get())).grid(row=r + 6, columnspan=2, sticky="w")
        self.land = tk.BooleanVar(value=cfg["land_only"])
        ttk.Checkbutton(f, text="Kara dışındaki şüpheli noktaları kullanma (harita görseli gerekir)", variable=self.land,
                        command=lambda: (cfg.update(land_only=self.land.get()), ov.apply_land_filter())
                        ).grid(row=r + 7, columnspan=2, sticky="w")
        ttk.Button(f, text="Harita kalibrasyonu…", command=lambda: CalibDialog(ov)).grid(row=r + 8, columnspan=2, sticky="we", pady=3)
        ttk.Button(f, text="Rota haritası…", command=lambda: RouteWindow(ov)).grid(row=r + 9, columnspan=2, sticky="we")
        ttk.Button(f, text="Varlıklar (canavar/NPC kimlikleri)…", command=lambda: EntitiesWindow(ov)).grid(row=r + 10, columnspan=2, sticky="we", pady=3)
        ttk.Button(f, text="Kapat", command=self.destroy).grid(row=r + 11, columnspan=2, pady=(8, 0))

    def _live(self, key):
        cfg = self.ov.cfg
        cfg[key] = round(self.vars[key].get(), 2)
        if key == "scale":
            self.ov._apply_scale()
        elif key == "alpha":
            self.ov.root.attributes("-alpha", cfg["alpha"])

    def _calib(self):
        self.ov.s.player_id = None
        self.ov.s.locked = False
        self.ov.s.start_calibration()
        self.ov.error = ""
        self.destroy()

    def _sound(self):
        self.ov.cfg["sound"] = self.snd.get()

    def _name(self):
        self.ov.cfg["char_name"] = self.name.get().strip()
        self.ov.s.char_name = self.ov.cfg["char_name"]


class CalibDialog(tk.Toplevel):
    """Bilinen bir yerin (kale, zindan girişi...) yanında dur, adını seç, 'Buradayım' de. 3+ yer gerekir."""

    def __init__(self, ov: Overlay):
        super().__init__(ov.root)
        self.ov, self.g = ov, ov.guide
        self.title("Harita kalibrasyonu")
        self.attributes("-topmost", True)
        f = ttk.Frame(self, padding=12)
        f.pack()
        if not self.g:
            ttk.Label(f, text="Harita verisi bulunamadı (a2radar/data).").pack()
            return
        ttk.Label(f, wraplength=340, text=(
            "Önerilen 6 yer listenin başında (Gravekeeper Camp, Moslan Forest Dry Sade, Faded Stump, Basfelt Ruins Cliff, "
            "Outskirts Stall, Abandoned Campsite).\n"
            "1) Oyunda o yerin hemen yanında dur.\n"
            "2) Zıpla/dash yap (konum güncellensin), adı seç, hemen 'Buradayım'a bas (koordinatı kayıtlı yerler).\n"
            "   Koordinatı kayıtlı değilse 'Rota haritasında işaretle…' ile haritada yerine tıkla.\n"
            "3) Birbirinden uzak en az 3 yer için tekrarla; 6'sı daha doğru sonuç verir.")).pack(anchor="w")
        self.name = tk.StringVar()
        names = mapcal.SUGGESTED + sorted(l[0] for l in self.g.landmarks if l[0] not in mapcal.SUGGESTED)
        ttk.Combobox(f, textvariable=self.name, values=names, width=46).pack(fill="x", pady=6)
        ttk.Button(f, text="Buradayım", command=self._add).pack(fill="x")
        ttk.Button(f, text="Rota haritasında işaretle…", command=lambda: RouteWindow(self.ov, self.name.get())).pack(fill="x", pady=(4, 0))
        self.info = ttk.Label(f, wraplength=340, foreground="#555")
        self.info.pack(anchor="w", pady=6)
        self.box = tk.Listbox(f, height=6)
        self.box.pack(fill="x")
        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=6)
        ttk.Button(bf, text="Sonuncuyu sil", command=lambda: (self.g.remove_last(), self._refresh())).pack(side="left")
        ttk.Button(bf, text="Hepsini sıfırla", command=lambda: (self.g.clear(), self._refresh())).pack(side="left", padx=6)
        self._refresh()
        self._poll()

    def _poll(self):
        if self.winfo_exists():
            snap = self.ov.s.snapshot()
            age = "—" if snap.pos_age is None else f"{snap.pos_age:.0f} sn önce"
            self.title(f"Harita kalibrasyonu · konum: {age}")
            self.after(500, self._poll)

    def _refresh(self):
        g = self.g
        self.box.delete(0, "end")
        errs = g.anchor_errors()
        for a in g.anchors:
            e = f"  · sapma {errs[a['name']]:.0f} m" if a["name"] in errs and not g.approximate else ""
            self.box.insert("end", f"{a['name']}  ({a['gx'] / 100:.0f}, {a['gy'] / 100:.0f}){e}")
        if g.default_tf:
            self.info.config(text="Varsayılan dönüşüm kullanılıyor (2 m/piksel, harita merkezi = dünya merkezi). "
                                  "Çalışır ama tahminidir; 3+ yer kaydedersen düzelir.")
        elif g.ready:
            q = "tahmini (3. yer ekle)" if g.approximate else f"hata ≈ {g.rms_m:.0f} m"
            self.info.config(text=f"Hazır: {len(g.anchors)} yer, {q}, ölçek ≈ {g.cm_per_px / 100:.2f} m/piksel. "
                                  "Hata 30 m'den büyükse yeri kontrol et.")
        else:
            need = "yerler birbirine çok yakın" if len(g.anchors) >= 2 else f"{len(g.anchors)}/3 yer"
            self.info.config(text=f"Henüz hazır değil: {need}.")

    def _add(self):
        snap = self.ov.s.snapshot()
        if snap.player_id is None:
            return self.info.config(text="Önce ana ekranda 'Karakterimi bul'u yap.")
        if snap.pos is None or snap.pos_age is None or snap.pos_age > 4:
            return self.info.config(text="Konum eski. Zıpla/dash yap, hemen tekrar bas.")
        try:
            self.g.add_anchor(self.name.get().strip(), snap.pos[0], snap.pos[1])
        except ValueError as e:
            return self.info.config(text=str(e))
        self._refresh()


class RouteWindow(tk.Toplevel):
    """Haritada tüm olası noktalar, kalan rota, oyuncu ve tarama yarıçapı."""

    SIZE, MAP = 660, 4096

    def __init__(self, ov: Overlay, calib_name: str = ""):
        super().__init__(ov.root)
        self.ov, self.g = ov, ov.guide
        self.title("Rota haritası")
        self.attributes("-topmost", True)
        self.k = self.SIZE / self.MAP
        self.bgphoto = None
        if ov.mapimg:
            from PIL import ImageTk
            self.bgphoto = ImageTk.PhotoImage(ov.mapimg.full(self.SIZE, dim=0.45))
        self.pending = None          # (ad, oyun_konumu): haritaya tıklanınca yer olarak kaydedilir
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.info = ttk.Label(top, text="")
        self.info.pack(side="left")
        ttk.Button(top, text="Ziyaretleri sıfırla", command=self._reset).pack(side="right")
        ttk.Button(top, text="Yarıçapı ölçüme ayarla", command=self._fit_radius).pack(side="right", padx=6)
        self.c = tk.Canvas(self, width=self.SIZE, height=self.SIZE, bg="#0e0d14", highlightthickness=0)
        self.c.pack()
        self.c.bind("<ButtonPress-1>", self._click)
        self.c.bind("<Motion>", self._motion)
        self.coord = ttk.Label(self, foreground="#444", font=("Consolas", 9))
        self.coord.pack(anchor="w", padx=6, before=self.c)      # haritanın üstünde
        self._cursor_txt = ""
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Yer:").pack(side="left")
        self.cal_name = tk.StringVar(value=calib_name or mapcal.SUGGESTED[0])
        names = mapcal.SUGGESTED + sorted(l[0] for l in (ov.guide.landmarks if ov.guide else []) if l[0] not in mapcal.SUGGESTED)
        ttk.Combobox(bar, textvariable=self.cal_name, values=names, width=34).pack(side="left", padx=4)
        ttk.Button(bar, text="Bulunduğum yeri kaydet → haritaya tıkla", command=self._arm).pack(side="left")
        self.note = ttk.Label(self, foreground="#a35", wraplength=self.SIZE)
        self.note.pack(anchor="w", padx=6)
        self.aoi = ttk.Label(self, foreground="#666")
        self.aoi.pack(anchor="w", padx=6, pady=4)
        if not ov.mapimg:
            self.note.config(text="Harita görseli yok: düz zeminde çiziliyor (README: altgard_map.png ekle).", foreground="#666")
        self._draw()

    def _arm(self):
        """Oyuncunun şu anki taze konumunu alır; sonraki harita tıklaması o yerin konumu olur."""
        if not self.g:
            return
        snap = self.ov.s.snapshot()
        if snap.player_id is None:
            return self.note.config(text="Önce ana ekranda 'Karakterimi bul'u yap.", foreground="#a35")
        if snap.pos is None or snap.pos_age is None or snap.pos_age > 4:
            return self.note.config(text="Konum eski. Zıpla/dash yap, hemen tekrar bas.", foreground="#a35")
        name = self.cal_name.get().strip()
        if not name:
            return self.note.config(text="Yer adı boş.", foreground="#a35")
        self.pending = (name, snap.pos[0], snap.pos[1])
        self.note.config(text=f"'{name}' için konum alındı. Haritada şu an bulunduğun yere tıkla.", foreground="#16a34a")

    def _click(self, e):
        if not (self.pending and self.g):
            return
        name, gx, gy = self.pending
        try:
            self.g.add_anchor_px(name, e.x / self.k, e.y / self.k, gx, gy)
        except ValueError as ex:
            return self.note.config(text=str(ex), foreground="#a35")
        self.pending = None
        g = self.g
        if g.ready:
            err = g.anchor_errors().get(name, 0)
            q = "tahmini (3. yer ekle)" if g.approximate else f"hata ≈ {g.rms_m:.0f} m, bu yer {err:.0f} m sapıyor"
            self.note.config(text=f"'{name}' kaydedildi · {len(g.anchors)} yer · {q}", foreground="#16a34a")
        else:
            self.note.config(text=f"'{name}' kaydedildi · {len(g.anchors)}/3 yer", foreground="#16a34a")

    def _motion(self, e):
        """İmlecin altındaki noktanın oyun dünyası koordinatı (X, Y metre)."""
        g = self.g
        if g and g.ready:
            gx, gy = g.to_game(e.x / self.k, e.y / self.k)
            self._cursor_txt = f"İmleç: X {gx / 100:.0f}  Y {gy / 100:.0f} m"
        else:
            self._cursor_txt = f"İmleç: harita {e.x / self.k:.0f}, {e.y / self.k:.0f} (kalibrasyon yok)"
        self._coords()

    def _coords(self):
        snap = self.ov.s.snapshot()
        me = f"Oyuncu: X {snap.pos[0] / 100:.0f}  Y {snap.pos[1] / 100:.0f}  Z {snap.pos[2] / 100:.0f} m" if snap.pos else "Oyuncu: konum yok"
        self.coord.config(text=(self._cursor_txt + "    " if self._cursor_txt else "") + me)

    def _reset(self):
        if self.g:
            self.g.reset_visits()

    def _fit_radius(self):
        fs = self.ov.first_seen
        if fs:
            self.ov.cfg["scan_m"] = float(max(20, min(500, round(max(fs) * 0.9))))

    def _xy(self, mx, my):
        return mx * self.k, my * self.k

    def _draw(self):
        if not self.winfo_exists():
            return
        c, g, ov = self.c, self.g, self.ov
        c.delete("all")
        snap = ov.s.snapshot()
        if self.bgphoto:
            c.create_image(0, 0, anchor="nw", image=self.bgphoto)
        if g:
            for a in g.anchors:     # kalibrasyon yerleri: yeşil baklava + ad
                x, y = self._xy(a["px"], a["py"])
                c.create_polygon(x, y - 6, x + 6, y, x, y + 6, x - 6, y, fill="#22c55e", outline="#ffffff")
                c.create_text(x + 9, y, anchor="w", text=a["name"], fill="#bbf7d0", font=("Segoe UI", 8, "bold"))
        if not (g and g.ready):
            c.create_text(self.SIZE / 2, 14, fill="#cfcfe0", text="Kalibrasyon yok: 2+ yer kaydet (altta 'Yer' + 'Bulunduğum yeri kaydet').")
            self.info.config(text="Kalibrasyon yok")
        else:
            rcm = ov.cfg["scan_m"] * 100
            if snap.pos:
                age = snap.pos_age
                g.update_visited(snap.pos, rcm, fresh=age is not None and age < 3)
            route = g.plan(snap.pos, rcm, snap.cubes) if snap.pos else []
            for i, (gx, gy) in enumerate(g._game):
                x, y = self._xy(*g.to_map(gx, gy))
                if i in g.off:      # kara dışı şüpheli nokta: soluk kırmızı artı
                    c.create_line(x - 2, y, x + 2, y, fill="#7a2a3a")
                    c.create_line(x, y - 2, x, y + 2, fill="#7a2a3a")
                    continue
                c.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill="#333" if i in g.visited else "#5b7fa6", outline="")
            if snap.pos and route:
                pts = [self._xy(*g.to_map(snap.pos[0], snap.pos[1]))] + [self._xy(*g.to_map(x, y)) for x, y, _ in route]
                for j in range(len(pts) - 1):
                    c.create_line(*pts[j], *pts[j + 1], fill="#38bdf8" if j < 25 else "#1f4c66", width=2 if j < 25 else 1)
                rpx = rcm / g.cm_per_px * self.k
                for n, (x, y) in enumerate(pts[1:26], 1):
                    c.create_oval(x - rpx, y - rpx, x + rpx, y + rpx, outline="#235a7a")
                    c.create_text(x, y, text=str(n), fill="#cde8f7", font=("Segoe UI", 7))
                x, y = pts[1]
                c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#fde047", width=2)
            for cube in snap.cubes:
                x, y = self._xy(*g.to_map(cube.x, cube.y))
                c.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#ef4444", outline="#fff")
            if snap.pos:
                x, y = self._xy(*g.to_map(snap.pos[0], snap.pos[1]))
                c.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#fff", outline="#22c55e", width=2)
            s = g.summary(snap.pos, rcm, snap.cubes) if snap.pos else None
            if not snap.pos:
                msg = "Konum bekleniyor"
            elif s is None:
                msg = "Rota bitti"
            else:
                msg = f"{s[0]} durak · {s[1]} aday · {s[2] / 1000:.1f} km · yarıçap {ov.cfg['scan_m']:.0f} m"
                if g.off:
                    msg += f" · {len(g.off)} şüpheli nokta elendi"
            self.info.config(text=msg)
        fs = ov.first_seen
        if fs:
            self.aoi.config(text="Küpün ilk görüldüğü mesafe (m), son: " + ", ".join(f"{d:.0f}" for d in fs[-6:]) +
                            f" · en büyük {max(fs):.0f} m")
        else:
            self.aoi.config(text="Henüz küp ilk görülme ölçümü yok (küp çıkınca burada görünür).")
        self._coords()
        self.after(500, self._draw)


def main():
    cfg = settings.load()
    state = State()
    state.char_name = cfg["char_name"]
    root = tk.Tk()
    ov = Overlay(root, state, cfg)
    cap = Capture(state, on_error=lambda m: setattr(ov, "error", m))
    cap.start()
    root.protocol("WM_DELETE_WINDOW", ov._close)
    try:
        root.mainloop()
    finally:
        cap.stop()
        settings.save(cfg)
