"""Hub sekmeleri: Combat, Kaynak Toplama, Radar Ayarları ve yardımcı diyaloglar."""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import audio, catalog, entities, routes, settings, statlearn
from .app import CalibDialog, RouteWindow
from .hub import ListPanel
from .mapview import MapView

NOTE_INPUT = ("Not: beceri ve alan ayarları öneri/uyarı içindir. Oyun otomatik tuş girdisini kabul etmediği için "
              "bu araç tuşlara basmaz; yön ve mesafe önerir.")


def _fmt(v):
    if v is None:
        return "—"
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-6 else f"{v:,.1f}"


def _num(text, default=0.0):
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
class StatLearnDialog(tk.Toplevel):
    """Ekrandaki değeri gir → paketlerde ara → değer değişince tekrar → tek aday kalınca kabul et."""

    def __init__(self, hub, key: str, label: str):
        super().__init__(hub.root)
        self.hub, self.key = hub, key
        self.title(f"Değer öğren: {label}")
        self.transient(hub.root)
        s = hub.state
        if s.learner is None:
            s.learner = statlearn.StatLearner()
        self.lr = s.learner
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Oyunda şu an görünen '{label}' değerini yaz, 'Ara'ya bas.\n"
                          "Sonra değeri değiştir (hasar al, mana harca…), yeni değeri yaz, tekrar 'Ara'.\n"
                          "İki aramada da eşleşen alan kalır; tek aday kalınca 'Kabul et'.",
                  justify="left", foreground="#444").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Değer:").pack(side="left")
        self.val = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.val, width=12)
        e.pack(side="left", padx=6)
        e.focus_set()
        e.bind("<Return>", lambda _e: self._search())
        ttk.Button(row, text="Ara (8 sn)", command=self._search).pack(side="left")
        ttk.Button(row, text="Sıfırla", command=self._reset).pack(side="left", padx=6)
        self.own = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Yalnız kendi paketlerim", variable=self.own).pack(side="left", padx=8)
        self.state_lbl = ttk.Label(f, text="Hazır", foreground="#666")
        self.state_lbl.pack(anchor="w")
        self.lb = tk.Listbox(f, height=9, exportselection=False, font=("Consolas", 9))
        self.lb.pack(fill="both", expand=True, pady=6)
        ttk.Button(f, text="Seçili adayı kabul et", command=self._accept).pack(anchor="e")
        self.cands: list = []
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._poll()

    def _search(self):
        v = _num(self.val.get(), None)
        if v is None or v != v:
            return self.state_lbl.config(text="Sayı gir.", foreground="#b91c1c")
        if self.hub.state.player_id is None and self.own.get():
            return self.state_lbl.config(text="Önce karakter bulunmalı (ya da 'Yalnız kendi paketlerim'i kapat).", foreground="#b91c1c")
        self.lr.start(self.key, v, 8.0)

    def _reset(self):
        self.lr.reset()
        self.lb.delete(0, "end")
        self.cands = []

    def _poll(self):
        if not self.winfo_exists():
            return
        lr = self.lr
        if lr.key == self.key:
            if lr.active:
                self.state_lbl.config(text=f"Aranıyor… {max(0, lr.until - time.time()):.0f} sn · {lr.windows} pencere tamam", foreground="#b45309")
            else:
                lr.finish()
                n = len(lr.candidates(self.own.get()))
                hint = " Değeri değiştirip tekrar ara." if n > 1 and lr.windows < 2 else ""
                self.state_lbl.config(text=f"{n} aday · {lr.windows} pencere.{hint}" if lr.windows else "Hazır",
                                      foreground="#16a34a" if n == 1 else "#444")
            self.cands = [c for c, _ in lr.candidates(self.own.get())][:60]
            items = [f"op {c[0]:<6} ofset {c[1]:+d}  {c[2]:<3} {'kendi' if c[3] else 'diğer'}  ×{n}" for c, n in lr.candidates(self.own.get())[:60]]
            sel = self.lb.curselection()
            self.lb.delete(0, "end")
            for it in items:
                self.lb.insert("end", it)
            if sel:
                self.lb.selection_set(sel[0])
        self.after(400, self._poll)

    def _accept(self):
        sel = self.lb.curselection()
        if not sel or sel[0] >= len(self.cands):
            return messagebox.showinfo("Değer", "Listeden bir aday seç.", parent=self)
        cand = self.cands[sel[0]]
        self.lr.accept(self.hub.state.statmap, self.key, cand)
        messagebox.showinfo("Değer", "Kaydedildi: bu alan artık canlı okunuyor.", parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
class SkillList(ttk.LabelFrame):
    """Beceri listesi (Saldırı / Buff): kullanılacaklar işaretlenir; ad ve tuş elle girilir."""

    def __init__(self, parent, hub, key: str, title: str, buff: bool):
        super().__init__(parent, text=title, padding=6)
        self.hub, self.key, self.buff = hub, key, buff
        cols = ("use", "name", "key") + (("interval",) if buff else ())
        self.tv = ttk.Treeview(self, columns=cols, show="headings", height=7, selectmode="browse")
        for c, h, w in (("use", "Kullan", 52), ("name", "Beceri", 150), ("key", "Tuş", 50), ("interval", "Süre (sn)", 70)):
            if c in cols:
                self.tv.heading(c, text=h)
                self.tv.column(c, width=w, anchor="w" if c == "name" else "center")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", self._toggle)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(4, 0))
        for text, cmd in (("Ekle", self._add), ("Düzenle", self._edit), ("Sil", self._del), ("↑", lambda: self._move(-1)), ("↓", lambda: self._move(1))):
            ttk.Button(row, text=text, width=7 if len(text) > 1 else 3, command=cmd).pack(side="left", padx=1)
        self.refresh()

    @property
    def items(self):
        return self.hub.profile[self.key]

    def refresh(self):
        self.tv.delete(*self.tv.get_children())
        for i, s in enumerate(self.items):
            vals = ("✔" if s.get("use", True) else "", s.get("name", ""), s.get("key", ""))
            if self.buff:
                vals += (_fmt(s.get("interval_s", 0)),)
            self.tv.insert("", "end", iid=str(i), values=vals)

    def _sel(self):
        s = self.tv.selection()
        return int(s[0]) if s else None

    def _toggle(self, _e=None):
        i = self._sel()
        if i is not None:
            self.items[i]["use"] = not self.items[i].get("use", True)
            self._changed(i)

    def _changed(self, sel=None):
        self.hub.save_profile()
        self.refresh()
        if sel is not None and self.tv.exists(str(sel)):
            self.tv.selection_set(str(sel))

    def _dialog(self, cur=None):
        d = tk.Toplevel(self)
        d.title("Beceri")
        d.transient(self.winfo_toplevel())
        d.grab_set()
        cur = cur or {}
        name, key, itv = tk.StringVar(value=cur.get("name", "")), tk.StringVar(value=cur.get("key", "")), tk.StringVar(value=_fmt(cur.get("interval_s", 60)))
        ttk.Label(d, text="Beceri adı").grid(row=0, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(d, textvariable=name, width=24).grid(row=0, column=1, padx=8)
        ttk.Label(d, text="Tuş").grid(row=1, column=0, padx=8, sticky="w")
        ttk.Entry(d, textvariable=key, width=8).grid(row=1, column=1, padx=8, pady=4, sticky="w")
        if self.buff:
            ttk.Label(d, text="Yenileme süresi (sn)").grid(row=2, column=0, padx=8, sticky="w")
            ttk.Entry(d, textvariable=itv, width=8).grid(row=2, column=1, padx=8, pady=4, sticky="w")
        out = []

        def ok():
            if not name.get().strip():
                return messagebox.showerror("Beceri", "Ad boş.", parent=d)
            out.append({"name": name.get().strip()[:40], "key": key.get().strip()[:12], "use": cur.get("use", True),
                        "interval_s": max(0.0, _num(itv.get(), 60.0)) if self.buff else 0.0})
            d.destroy()

        ttk.Button(d, text="Kaydet", command=ok).grid(row=3, columnspan=2, pady=8)
        d.wait_window()
        return out[0] if out else None

    def _add(self):
        s = self._dialog()
        if s:
            self.items.append(s)
            self._changed(len(self.items) - 1)

    def _edit(self):
        i = self._sel()
        if i is not None:
            s = self._dialog(self.items[i])
            if s:
                self.items[i] = s
                self._changed(i)

    def _del(self):
        i = self._sel()
        if i is not None:
            del self.items[i]
            self._changed()

    def _move(self, d):
        i = self._sel()
        if i is not None and 0 <= i + d < len(self.items):
            self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
            self._changed(i + d)


class AreaDialog(tk.Toplevel):
    def __init__(self, hub, area=None, on_save=None):
        super().__init__(hub.root)
        self.hub, self.on_save = hub, on_save
        self.title("Farm alanı")
        self.transient(hub.root)
        self.grab_set()
        a = area or {"name": f"Alan {len(hub.profile['farm_areas']) + 1}", "radius_m": 50.0, "monsters": []}
        if "x_m" not in a:
            pos = hub.state.pos
            a["x_m"], a["y_m"] = (round(pos[0] / 100), round(pos[1] / 100)) if pos else (0, 0)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        self.v = {k: tk.StringVar(value=_fmt(a[k]) if k != "name" else a[k]) for k in ("name", "x_m", "y_m", "radius_m")}
        for i, (k, lbl) in enumerate((("name", "Ad"), ("x_m", "Merkez X (m)"), ("y_m", "Merkez Y (m)"), ("radius_m", "Yarıçap (m)"))):
            ttk.Label(f, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(f, textvariable=self.v[k], width=18).grid(row=i, column=1, sticky="w")
        ttk.Button(f, text="Konumumu al", command=self._here).grid(row=1, column=2, rowspan=2, padx=6)
        ttk.Label(f, text="Bu alanda avlanacak canavarlar").grid(row=4, columnspan=3, sticky="w", pady=(8, 2))
        self.q = tk.StringVar()
        ttk.Entry(f, textvariable=self.q).grid(row=5, columnspan=3, sticky="we")
        self.q.trace_add("write", lambda *_: self._fill())
        self.lb = tk.Listbox(f, selectmode="extended", exportselection=False, height=10, width=34)
        self.lb.grid(row=6, columnspan=3, sticky="nsew", pady=4)
        self.chosen = set(a.get("monsters", []))
        self.lb.bind("<<ListboxSelect>>", self._sel)
        self.ents = catalog.build(hub.labels, hub.db, "canavar")
        self.vis: list = []
        self._fill()
        ttk.Button(f, text="Kaydet", command=lambda: self._ok(a)).grid(row=7, columnspan=3, pady=6)
        self.wait_visibility()

    def _here(self):
        pos = self.hub.state.pos
        if pos:
            self.v["x_m"].set(_fmt(round(pos[0] / 100)))
            self.v["y_m"].set(_fmt(round(pos[1] / 100)))

    def _fill(self):
        self.vis = catalog.filter_entries(self.ents, self.q.get())
        self.lb.delete(0, "end")
        for i, e in enumerate(self.vis):
            self.lb.insert("end", e.name)
            if e.name in self.chosen:
                self.lb.selection_set(i)

    def _sel(self, _e=None):
        sel = set(self.lb.curselection())
        for i, e in enumerate(self.vis):
            (self.chosen.add if i in sel else self.chosen.discard)(e.name)

    def _ok(self, a):
        name = self.v["name"].get().strip()
        if not name:
            return messagebox.showerror("Alan", "Ad boş.", parent=self)
        a.update(name=name[:40], x_m=_num(self.v["x_m"].get()), y_m=_num(self.v["y_m"].get()),
                 radius_m=max(5.0, min(2000.0, _num(self.v["radius_m"].get(), 50.0))), monsters=sorted(self.chosen))
        self.on_save(a)
        self.destroy()


class StatBar(tk.Canvas):
    """Düz renkli yatay çubuk (ttk Progressbar teması renk vermediği için)."""

    def __init__(self, parent, color):
        super().__init__(parent, height=16, highlightthickness=1, highlightbackground="#bbb", bg="#e5e7eb")
        self.color, self.frac = color, 0.0
        self.bind("<Configure>", lambda _e: self.set(self.frac))

    def set(self, frac):
        self.frac = max(0.0, min(1.0, frac))
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.create_rectangle(0, 0, w * self.frac, h, fill=self.color, outline="")


# ---------------------------------------------------------------------------
class CombatTab(ttk.Frame):
    BARS = (("hp", "hp_max", "Can", "#ef4444"), ("mp", "mp_max", "Mana", "#3b82f6"), ("energy", None, "Enerji", "#eab308"))

    def __init__(self, parent, hub):
        super().__init__(parent, padding=8)
        self.hub = hub
        left = ttk.LabelFrame(self, text="Karakter", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        mid = ttk.Frame(self)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        right = ttk.LabelFrame(self, text="Farm alanları", padding=8)
        right.grid(row=0, column=2, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)

        self.name_lbl = ttk.Label(left, font=("Segoe UI", 12, "bold"))
        self.name_lbl.pack(anchor="w")
        row = ttk.Frame(left)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Sınıf:").pack(side="left")
        self.cls = tk.StringVar(value=hub.profile.get("char_class", ""))
        e = ttk.Entry(row, textvariable=self.cls, width=18)
        e.pack(side="left", padx=4)
        e.bind("<FocusOut>", lambda _e: self._save_cls())
        e.bind("<Return>", lambda _e: self._save_cls())
        self.bars = {}
        for key, mx, label, color in self.BARS:
            ttk.Label(left, text=label).pack(anchor="w", pady=(8, 0))
            pb = StatBar(left, color)
            pb.pack(fill="x")
            pb.peak = 0.0
            lbl = ttk.Label(left, text="—", foreground="#444")
            lbl.pack(anchor="w")
            self.bars[key] = (pb, lbl, mx)
        ttk.Separator(left).pack(fill="x", pady=8)
        self.rows = {}
        grid = ttk.Frame(left)
        grid.pack(fill="x")
        for i, (key, label) in enumerate(statlearn.STATS):
            ttk.Label(grid, text=label).grid(row=i, column=0, sticky="w", pady=1)
            v = ttk.Label(grid, text="—", width=12, anchor="e")
            v.grid(row=i, column=1, padx=6)
            ttk.Button(grid, text="Öğren…", width=8, command=lambda k=key, l=label: StatLearnDialog(hub, k, l)).grid(row=i, column=2)
            self.rows[key] = v
        self.src = ttk.Label(left, foreground="#777", wraplength=280, justify="left")
        self.src.pack(anchor="w", pady=8)

        self.attack = SkillList(mid, hub, "attack_skills", "Saldırı becerileri", buff=False)
        self.attack.pack(fill="both", expand=True)
        self.buff = SkillList(mid, hub, "buff_skills", "Buff becerileri", buff=True)
        self.buff.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(mid, text=NOTE_INPUT, foreground="#a35", wraplength=420, justify="left").pack(anchor="w", pady=6)

        self.tv = ttk.Treeview(right, columns=("name", "x", "y", "r", "mon"), show="headings", height=10, selectmode="browse")
        for c, h, w in (("name", "Alan", 90), ("x", "X", 56), ("y", "Y", 56), ("r", "R", 40), ("mon", "Canavarlar", 140)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", lambda _e: self._edit())
        row = ttk.Frame(right)
        row.pack(fill="x", pady=4)
        for text, cmd in (("Bulunduğum yerden ekle", self._add_here), ("Düzenle…", self._edit), ("Sil", self._del)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Label(right, text="Alanlar Ana Sayfa haritasında mor daire olarak görünür.", foreground="#666").pack(anchor="w")
        self.refresh_areas()
        self._tick()

    def _save_cls(self):
        self.hub.profile["char_class"] = self.cls.get().strip()[:40]
        self.hub.save_profile()

    # --- istatistikler ---
    def _tick(self):
        if not self.winfo_exists():
            return
        hub = self.hub
        s = hub.state
        if self.winfo_viewable():
            nm = s.player_names.get(s.player_id) or hub.cfg["char_name"] or "Karakter adı bilinmiyor"
            self.name_lbl.config(text=nm)
            stats = s.stats
            for key, (pb, lbl, mx) in self.bars.items():
                v = stats.get(key, (None,))[0]
                m = stats.get(mx, (None,))[0] if mx else None
                if v is None:
                    pb.set(0)
                    text = "— (değer haritalanmamış: aşağıdaki 'Öğren…')"
                else:
                    pb.peak = max(pb.peak, v)
                    top = m or pb.peak
                    pb.set(v / top if top else 0)
                    text = f"{_fmt(v)} / {_fmt(m)}" if m else f"{_fmt(v)}  (azami bilinmiyor; görülen en yüksek {_fmt(pb.peak)})"
                lbl.config(text=text)
            for key, lbl in self.rows.items():
                v = stats.get(key)
                lbl.config(text=_fmt(v[0]) if v else "—")
            mapped = sum(len(v) for v in s.statmap.values())
            self.src.config(text=f"{mapped} alan haritalanmış. Değerler oyun paketlerinden canlı okunur; "
                                 "haritalanmamış alanlar için 'Öğren…' ile ekrandaki sayıyı girerek paketi buldur.")
        self.after(1000, self._tick)

    # --- alanlar ---
    def refresh_areas(self):
        self.tv.delete(*self.tv.get_children())
        for i, a in enumerate(self.hub.profile["farm_areas"]):
            self.tv.insert("", "end", iid=str(i), values=(a.get("name", ""), _fmt(a.get("x_m")), _fmt(a.get("y_m")),
                                                          _fmt(a.get("radius_m")), ", ".join(a.get("monsters", []))))

    def _sel(self):
        s = self.tv.selection()
        return int(s[0]) if s else None

    def _save_area(self, idx, a):
        areas = self.hub.profile["farm_areas"]
        if idx is None:
            areas.append(a)
        else:
            areas[idx] = a
        self.hub.save_profile()
        self.refresh_areas()

    def _add_here(self):
        if self.hub.state.pos is None:
            return messagebox.showinfo("Alan", "Konum bilinmiyor.", parent=self)
        AreaDialog(self.hub, None, lambda a: self._save_area(None, a))

    def _edit(self):
        i = self._sel()
        if i is not None:
            AreaDialog(self.hub, dict(self.hub.profile["farm_areas"][i]), lambda a, i=i: self._save_area(i, a))

    def _del(self):
        i = self._sel()
        if i is not None:
            del self.hub.profile["farm_areas"][i]
            self.hub.save_profile()
            self.refresh_areas()

    def on_profile_changed(self):
        self.cls.set(self.hub.profile.get("char_class", ""))
        self.attack.refresh()
        self.buff.refresh()
        self.refresh_areas()


# ---------------------------------------------------------------------------
class GatherTab(ttk.Frame):
    """Kaynak toplama: toplanacak kaynaklar, rota çizme/kaydetme/geçiş. Asistan sıradaki durağı önerir."""

    SNAP_PX = 14

    def __init__(self, parent, hub):
        super().__init__(parent, padding=6)
        self.hub = hub
        self.cur: str | None = None
        self.sel: set[str] = set(hub.profile["gather"]["resources"])

        left = ttk.Frame(self, width=270)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        rf = ttk.LabelFrame(left, text="Kayıtlı rotalar", padding=6)
        rf.pack(fill="x")
        self.rl = tk.Listbox(rf, height=6, exportselection=False, activestyle="none")
        self.rl.pack(fill="x")
        self.rl.bind("<<ListboxSelect>>", self._pick_route)
        row = ttk.Frame(rf)
        row.pack(fill="x", pady=3)
        for text, cmd in (("Yeni", self._new), ("Adlandır", self._rename), ("Kopyala", self._copy), ("Sil", self._delete)):
            ttk.Button(row, text=text, width=8, command=cmd).pack(side="left", padx=1)
        ttk.Button(rf, text="★ Asistana ver (aktif rota)", command=self._activate).pack(fill="x")
        self.loop = tk.BooleanVar(value=False)
        ttk.Checkbutton(rf, text="Döngü (sondan başa)", variable=self.loop, command=self._loop).pack(anchor="w")

        gf = ttk.LabelFrame(left, text="Toplanacak kaynaklar", padding=4)
        gf.pack(fill="both", expand=True, pady=(8, 0))
        self.res = ListPanel(gf, hub, "kaynak", "Kaynak türleri", self.sel, on_change=self._res_changed)
        self.res.pack(fill="both", expand=True)
        row = ttk.Frame(gf)
        row.pack(fill="x")
        ttk.Label(row, text="Yarıçap (m):").pack(side="left")
        self.rad = tk.StringVar(value=_fmt(hub.profile["gather"].get("radius_m", 150.0)))
        sp = ttk.Spinbox(row, from_=10, to=5000, increment=10, width=7, textvariable=self.rad, command=self._radius)
        sp.pack(side="left", padx=4)
        sp.bind("<FocusOut>", lambda _e: self._radius())

        right = ttk.LabelFrame(self, text="Duraklar", padding=6, width=250)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        self.tv = ttk.Treeview(right, columns=("n", "x", "y", "ad"), show="headings", height=14, selectmode="browse")
        for c, h, w in (("n", "#", 30), ("x", "X", 52), ("y", "Y", 52), ("ad", "Ad", 80)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, anchor="w" if c == "ad" else "center")
        self.tv.pack(fill="both", expand=True)
        row = ttk.Frame(right)
        row.pack(fill="x", pady=3)
        for text, cmd in (("↑", lambda: self._move(-1)), ("↓", lambda: self._move(1)), ("Sil", self._del_wp)):
            ttk.Button(row, text=text, width=6, command=cmd).pack(side="left", padx=1)
        self.info = ttk.Label(right, foreground="#444", wraplength=230)
        self.info.pack(anchor="w", pady=4)

        mid = ttk.Frame(self)
        mid.pack(side="left", fill="both", expand=True, padx=8)
        bar = ttk.Frame(mid)
        bar.pack(fill="x", pady=(0, 4))
        for text, cmd in (("Seçili kaynaklardan rota üret", self._generate), ("Yakın sırayla sırala", self._sort),
                          ("Ters çevir", self._reverse), ("Temizle", self._clear)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        self.map = MapView(mid, hub, self.layers, click_cb=self._click, rclick_cb=self._rclick)
        self.map.pack(fill="both", expand=True)
        ttk.Label(mid, text="Haritaya tıkla: durak ekle (kaynak noktasına yapışır) · sağ tık: en yakın durağı sil · "
                            "değişiklikler profile otomatik kaydedilir.", foreground="#666").pack(anchor="w")
        self.refresh_routes()

    # --- veri ---
    @property
    def pts(self) -> list:
        if not self.cur:
            return []
        return self.hub.profile["routes"].setdefault(self.cur, [])

    def _save(self):
        self.hub.save_profile()
        self.refresh_wp()
        if self.cur and self.cur == self.hub.profile.get("active_route") and self.hub.assist_on.get():
            self.hub.walker.reset(self.hub.active_route_points())

    def refresh_lists(self):
        self.res.refresh(force=True)

    def on_profile_changed(self):
        self.sel.clear()
        self.sel.update(self.hub.profile["gather"]["resources"])
        self.rad.set(_fmt(self.hub.profile["gather"].get("radius_m", 150.0)))
        self.cur = None
        self.res.refresh(force=True)
        self.refresh_routes()

    def refresh_routes(self):
        names = sorted(self.hub.profile["routes"])
        if self.cur not in names:
            self.cur = names[0] if names else None
        act = self.hub.profile.get("active_route", "")
        self.rl.delete(0, "end")
        for i, n in enumerate(names):
            self.rl.insert("end", ("★ " if n == act else "   ") + n)
            if n == self.cur:
                self.rl.selection_set(i)
        self.refresh_wp()

    def refresh_wp(self):
        self.tv.delete(*self.tv.get_children())
        for i, p in enumerate(self.pts):
            self.tv.insert("", "end", iid=str(i), values=(i + 1, f"{p['x_m']:.0f}", f"{p['y_m']:.0f}", p.get("label", "")))
        n = len(self.pts)
        self.info.config(text=f"{n} durak · {routes.length_m(self.pts) / 1000:.2f} km" if n else
                         ("Rota seçili değil: 'Yeni' ile oluştur." if not self.cur else "Rota boş."))
        self.loop.set(bool(self.hub.profile.get("loop", {}).get(self.cur, False)) if self.cur else False)

    def _pick_route(self, _e=None):
        s = self.rl.curselection()
        names = sorted(self.hub.profile["routes"])
        if s and s[0] < len(names):
            self.cur = names[s[0]]
            self.refresh_wp()

    # --- rota yönetimi ---
    def _new(self):
        name = self.hub._ask_name("Yeni rota", f"Rota {len(self.hub.profile['routes']) + 1}")
        if name and name not in self.hub.profile["routes"]:
            self.hub.profile["routes"][name] = []
            self.cur = name
            self.hub.save_profile()
            self.refresh_routes()
        elif name:
            messagebox.showinfo("Rota", "Bu adla bir rota var.", parent=self)

    def _rename(self):
        if not self.cur:
            return
        name = self.hub._ask_name("Rotayı adlandır", self.cur)
        r = self.hub.profile["routes"]
        if name and name != self.cur and name not in r:
            r[name] = r.pop(self.cur)
            if self.hub.profile.get("active_route") == self.cur:
                self.hub.profile["active_route"] = name
            self.cur = name
            self.hub.save_profile()
            self.refresh_routes()

    def _copy(self):
        if not self.cur:
            return
        name = self.hub._ask_name("Rotayı kopyala", self.cur + " 2")
        r = self.hub.profile["routes"]
        if name and name not in r:
            import copy
            r[name] = copy.deepcopy(r[self.cur])
            self.cur = name
            self.hub.save_profile()
            self.refresh_routes()

    def _delete(self):
        if self.cur and messagebox.askyesno("Rota sil", f"'{self.cur}' silinsin mi?", parent=self):
            self.hub.profile["routes"].pop(self.cur, None)
            if self.hub.profile.get("active_route") == self.cur:
                self.hub.profile["active_route"] = ""
            self.cur = None
            self.hub.save_profile()
            self.refresh_routes()

    def _activate(self):
        if not self.cur:
            return
        self.hub.profile["active_route"] = self.cur
        self.hub.save_profile()
        self.hub.walker.reset(self.hub.active_route_points())
        self.hub.walker.loop = bool(self.hub.profile.get("loop", {}).get(self.cur, False))
        self.hub.assist_on.set(True)
        self.refresh_routes()

    def _loop(self):
        if self.cur:
            self.hub.profile.setdefault("loop", {})[self.cur] = self.loop.get()
            self.hub.walker.loop = self.loop.get()
            self.hub.save_profile()

    # --- kaynak seçimi ---
    def _res_changed(self):
        self.hub.profile["gather"]["resources"] = sorted(self.sel)
        self.hub.save_profile()

    def _radius(self):
        self.hub.profile["gather"]["radius_m"] = max(10.0, min(5000.0, _num(self.rad.get(), 150.0)))
        self.hub.save_profile()

    def _selected_points(self):
        out = []
        for e in self.res.entries:
            if e.name in self.sel:
                out += [(x / 100, y / 100, e.name) for x, y, z, n, npc in self.hub.db.points(e.npc_ids)]
        return out

    # --- düzenleme ---
    def _ensure_route(self):
        if not self.cur:
            name = "Rota 1"
            i = 1
            while name in self.hub.profile["routes"]:
                i += 1
                name = f"Rota {i}"
            self.hub.profile["routes"][name] = []
            self.cur = name
            self.refresh_routes()

    def _click(self, gx, gy, ex, ey, view):
        self._ensure_route()
        best, bd = None, self.SNAP_PX
        for x, y, name in self._selected_points():
            sx, sy = view.to_px(x * 100, y * 100)
            d = math.hypot(sx - ex, sy - ey)
            if d < bd:
                best, bd = (x, y, name), d
        wp = {"x_m": round(best[0], 1), "y_m": round(best[1], 1), "label": best[2]} if best else {"x_m": round(gx, 1), "y_m": round(gy, 1), "label": ""}
        self.pts.append(wp)
        self._save()

    def _rclick(self, gx, gy, ex, ey, view):
        if not self.pts:
            return
        ds = [(math.hypot(view.to_px(p["x_m"] * 100, p["y_m"] * 100)[0] - ex, view.to_px(p["x_m"] * 100, p["y_m"] * 100)[1] - ey), i)
              for i, p in enumerate(self.pts)]
        d, i = min(ds)
        if d < 20:
            del self.pts[i]
            self._save()

    def _sel_wp(self):
        s = self.tv.selection()
        return int(s[0]) if s else None

    def _del_wp(self):
        i = self._sel_wp()
        if i is not None:
            del self.pts[i]
            self._save()

    def _move(self, d):
        i = self._sel_wp()
        if i is not None and 0 <= i + d < len(self.pts):
            self.pts[i], self.pts[i + d] = self.pts[i + d], self.pts[i]
            self._save()
            self.tv.selection_set(str(i + d))

    def _start_xy(self):
        pos = self.hub.state.pos
        return (pos[0] / 100, pos[1] / 100) if pos else ((self.pts[0]["x_m"], self.pts[0]["y_m"]) if self.pts else (0.0, 0.0))

    def _generate(self):
        pts = self._selected_points()
        if not pts:
            return messagebox.showinfo("Rota", "Önce soldan kaynak türü seç. (Seçili türler için bilinen spawn noktası yoksa "
                                               "bölgede gezerek öğrenilmesini bekle.)", parent=self)
        start = self._start_xy()
        r = float(self.hub.profile["gather"].get("radius_m", 150.0))
        if self.hub.state.pos is not None:
            near = [p for p in pts if math.hypot(p[0] - start[0], p[1] - start[1]) <= r]
            if near:
                pts = near
        pts = pts[:300]
        self._ensure_route()
        self.cur_pts = [{"x_m": round(x, 1), "y_m": round(y, 1), "label": n} for x, y, n in pts]
        self.hub.profile["routes"][self.cur] = routes.order_nearest(start, self.cur_pts)
        self._save()

    def _sort(self):
        if self.pts:
            self.hub.profile["routes"][self.cur] = routes.order_nearest(self._start_xy(), self.pts)
            self._save()

    def _reverse(self):
        if self.pts:
            self.hub.profile["routes"][self.cur] = list(reversed(self.pts))
            self._save()

    def _clear(self):
        if self.cur and self.pts and messagebox.askyesno("Rota", "Tüm duraklar silinsin mi?", parent=self):
            self.hub.profile["routes"][self.cur] = []
            self._save()

    def layers(self):
        hub = self.hub
        act = hub.assist_on.get() and self.cur == hub.profile.get("active_route")
        res = [(x * 100, y * 100, 1, n) for x, y, n in self._selected_points()]
        return {"resources": res, "route": self.pts, "route_i": hub.walker.i if act else -1,
                "route_from_player": act, "areas": hub.profile["farm_areas"],
                "target": hub.assist_target if hub.assist_on.get() else None}


# ---------------------------------------------------------------------------
class RadarTab(ttk.Frame):
    """Radar ayarları: varsayılan Hidden Cube radarı. Overlay ayarları canlı uygulanır."""

    ROWS = (("scale", "Boyut", .75, 1.5), ("alpha", "Saydamlık", .2, 1), ("volume", "Ses", 0, 1),
            ("range_m", "Menzil (m)", 20, 20000), ("scan_m", "Tarama yarıçapı (m)", 20, 500), ("map_m", "Harita yakınlığı (m)", 100, 2000))

    def __init__(self, parent, hub):
        super().__init__(parent, padding=14)
        self.hub = hub
        ov = hub.ov
        cfg = hub.cfg
        ttk.Label(self, text="Hidden Cube radarı", font=("Segoe UI", 13, "bold")).grid(row=0, columnspan=3, sticky="w")
        ttk.Label(self, text="Radar yer paylaşımı küpleri bulur; bu ayarlar hemen uygulanır.", foreground="#666").grid(row=1, columnspan=3, sticky="w", pady=(0, 8))
        self.vars = {}
        for i, (key, label, lo, hi) in enumerate(self.ROWS, start=2):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w")
            v = tk.DoubleVar(value=cfg[key])
            self.vars[key] = v
            ttk.Scale(self, from_=lo, to=hi, variable=v, length=260, command=lambda _=0, k=key: self._live(k)).grid(row=i, column=1, pady=3, padx=8)
            self.vars[key + "_lbl"] = ttk.Label(self, text=_fmt(cfg[key]), width=8)
            self.vars[key + "_lbl"].grid(row=i, column=2)
        r = 2 + len(self.ROWS)
        self.snd = tk.BooleanVar(value=cfg["sound"])
        ttk.Checkbutton(self, text="Sesli uyarı", variable=self.snd, command=lambda: cfg.update(sound=self.snd.get())).grid(row=r, columnspan=3, sticky="w", pady=(8, 0))
        self.prof = tk.StringVar(value=cfg["profile"])
        cb = ttk.Combobox(self, values=list(audio.PROFILES), textvariable=self.prof, state="readonly", width=20)
        cb.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=3)
        cb.bind("<<ComboboxSelected>>", lambda _e: (cfg.update(profile=self.prof.get()), audio.play(self.prof.get(), cfg["volume"])))
        self.flags = {}
        for j, (key, text) in enumerate((("route_on", "Arama rotası (küp yokken sonraki durağa yönlendir)"),
                                          ("minimap_on", "Radarın altında yol haritası paneli"),
                                          ("land_only", "Kara dışındaki şüpheli noktaları kullanma"))):
            v = tk.BooleanVar(value=cfg[key])
            self.flags[key] = v
            ttk.Checkbutton(self, text=text, variable=v, command=lambda k=key: self._flag(k)).grid(row=r + 2 + j, columnspan=3, sticky="w")
        r += 5
        ttk.Label(self, text="Karakter adı (isteğe bağlı)").grid(row=r, column=0, sticky="w", pady=(8, 0))
        self.name = tk.StringVar(value=cfg["char_name"])
        e = ttk.Entry(self, textvariable=self.name, width=24)
        e.grid(row=r, column=1, sticky="w", padx=8, pady=(8, 0))
        e.bind("<KeyRelease>", lambda _e: (cfg.update(char_name=self.name.get().strip()), setattr(hub.state, "char_name", cfg["char_name"])))
        btns = ttk.Frame(self)
        btns.grid(row=r + 1, columnspan=3, sticky="w", pady=10)
        for text, cmd in (("Radar yer paylaşımını aç/gizle", hub.toggle_overlay), ("Harita kalibrasyonu…", lambda: CalibDialog(ov)),
                          ("Rota haritası…", lambda: RouteWindow(ov)), ("Varsayılana dön (Hidden Cube)", self._defaults)):
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=3)
        ttk.Separator(self).grid(row=r + 2, columnspan=3, sticky="we", pady=6)
        self.data_lbl = ttk.Label(self, foreground="#444", justify="left")
        self.data_lbl.grid(row=r + 3, columnspan=3, sticky="w")
        row = ttk.Frame(self)
        row.grid(row=r + 4, columnspan=3, sticky="w", pady=6)
        ttk.Button(row, text="Spawn kaydını içe aktar", command=self._import).pack(side="left", padx=3)
        ttk.Button(row, text="Öğrenilmiş değerleri sıfırla", command=self._reset_stats).pack(side="left", padx=3)
        self._info()

    def _live(self, key):
        cfg, ov = self.hub.cfg, self.hub.ov
        cfg[key] = round(self.vars[key].get(), 2)
        self.vars[key + "_lbl"].config(text=_fmt(cfg[key]))
        if key == "scale":
            ov._apply_scale()
        elif key == "alpha":
            self.hub.ov_top.attributes("-alpha", cfg["alpha"])

    def _flag(self, key):
        self.hub.cfg[key] = self.flags[key].get()
        if key == "land_only":
            self.hub.ov.apply_land_filter()

    def _defaults(self):
        if not messagebox.askyesno("Varsayılan", "Radar ayarları varsayılan değerlere dönsün mü?", parent=self):
            return
        for k in ("alpha", "scale", "sound", "volume", "profile", "range_m", "route_on", "scan_m", "minimap_on", "map_m", "land_only"):
            self.hub.cfg[k] = settings.DEFAULTS[k]
        for k in ("scale", "alpha", "volume", "range_m", "scan_m", "map_m"):
            self.vars[k].set(self.hub.cfg[k])
            self._live(k)
        self.snd.set(self.hub.cfg["sound"])
        self.prof.set(self.hub.cfg["profile"])
        for k, v in self.flags.items():
            v.set(self.hub.cfg[k])
        self.hub.ov.apply_land_filter()

    def _info(self):
        if not self.winfo_exists():
            return
        db = self.hub.db
        n = sum(db.count(i) for i in db.npc_ids())
        self.data_lbl.config(text=f"Öğrenilmiş spawn noktaları: {n} nokta, {len(db.npc_ids())} tür\n"
                                  f"Haritalanmış oyuncu değerleri: {sum(len(v) for v in self.hub.state.statmap.values())}\n"
                                  f"Harita görseli: {'var' if self.hub.mapimg else 'yok (a2radar/data/altgard_map.png)'}")
        self.after(3000, self._info)

    def _import(self):
        log = self.hub.db.path.with_name("spawn_log.json")
        n = self.hub.db.import_log(log)
        messagebox.showinfo("Spawn", f"{n} spawn kaydı alındı." if log.exists() else "spawn_log.json bulunamadı (önce 'Tanı dışa aktar').", parent=self)

    def _reset_stats(self):
        if messagebox.askyesno("Değerler", "Öğrenilmiş can/mana alanları silinsin mi?", parent=self):
            self.hub.state.statmap.clear()
            self.hub.state.stats.clear()
            statlearn.save_map({})
