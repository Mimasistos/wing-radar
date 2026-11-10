"""Ortak arayüz: radar + farm yardımcısı. Önce karakter bulunur, sonra 4 sekmeli pencereye dönüşür.

Her şey pasif dinlemedir; oyuna girdi gönderilmez. 'Asistan' yön/mesafe önerir, tuşlara basmaz.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

from . import app, catalog, entities, mapcal, mapimg, profiles, routes, settings, spawndb, statlearn
from .capture import Capture
from .mapview import MapView
from .state import State
from .world import World

TITLE = "Wing Hub — Radar + Farm"
KINDS_UI = entities.KINDS


class Hub:
    def __init__(self, root: tk.Tk, start_capture: bool = True):
        self.root = root
        self.cfg = settings.load()
        self.state = State()
        self.state.char_name = self.cfg["char_name"]
        self.labels = entities.Labels()
        self.db = spawndb.SpawnDB()
        self.world = World(labels=self.labels, state=self.state)
        self.state.entities.on_spawn = self.db.record
        self.state.statmap = statlearn.load_map()
        self.store = profiles.ProfileStore()
        self.profile = self.store.load(self.cfg["hub_profile"]) if profiles.valid_name(self.cfg["hub_profile"]) else profiles.blank()
        try:
            self.guide = mapcal.MapGuide(prior=mapcal.PRIOR)
        except (OSError, ValueError):
            self.guide = None
        self.mapimg = mapimg.load()
        self.show_live = tk.BooleanVar(value=True)
        self.trail: deque = deque(maxlen=80)
        self.sel_monsters: set[str] = set()
        self.sel_resources: set[str] = set()
        self.walker = routes.Walker()
        self.assist_on = tk.BooleanVar(value=False)
        self.assist_text = ""
        self.assist_target = None
        self.error = ""
        self.main = None
        self.tabs: dict = {}
        self.ov = None
        self.cap = None
        self._import_old_log()
        self._make_overlay()
        if start_capture:
            self.cap = Capture(self.state, on_error=self._err)
            self.cap.start()
        root.title(TITLE)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(500, self._tick)
        if self.state.player_id is not None and self.state.pos is not None:
            self.show_main()
        else:
            self.show_find()

    # --- hazırlık ---
    def _err(self, msg):
        self.error = msg
        if self.ov:
            self.ov.error = msg

    def _import_old_log(self):
        """İlk açılışta dışa aktarılmış spawn kaydı varsa veritabanını onunla tohumla (bir kez)."""
        flag = self.db.path.with_name("spawns.seeded")
        if flag.exists() or self.db.npc_ids():
            return
        log = self.db.path.with_name("spawn_log.json")
        try:
            if log.exists():
                self.db.import_log(log)
            flag.write_text("1", "utf-8")
        except OSError:
            pass

    def _make_overlay(self):
        """Radar yer paylaşımı: gizli bir Toplevel; kalibrasyon, küp uyarıları ve harita rehberi hep çalışır."""
        top = tk.Toplevel(self.root)
        self.ov = app.Overlay(top, self.state, self.cfg)
        self.ov.labels = self.labels
        self.ov.guide = self.guide or self.ov.guide
        self.ov._close = self.hide_overlay
        top.withdraw()
        self.ov_top = top

    def overlay_visible(self) -> bool:
        return self.ov_top.state() != "withdrawn"

    def show_overlay(self):
        self.ov_top.deiconify()
        self.ov_top.lift()

    def hide_overlay(self):
        settings.save(self.cfg)
        self.ov_top.withdraw()

    def toggle_overlay(self):
        (self.hide_overlay if self.overlay_visible() else self.show_overlay)()

    # --- ekranlar ---
    def _clear(self):
        for w in self.root.winfo_children():
            if w is not self.ov_top:
                w.destroy()

    def show_find(self):
        self._clear()
        self.main = None
        self.root.geometry("560x430")
        self.root.minsize(520, 400)
        FindPanel(self.root, self, on_done=self.show_main).pack(fill="both", expand=True)

    def show_main(self):
        from .hubtabs import CombatTab, GatherTab, RadarTab
        self._clear()
        self.root.geometry("1320x840")
        self.root.minsize(1100, 680)
        f = ttk.Frame(self.root)
        f.pack(fill="both", expand=True)
        self.main = f
        self._profile_bar(f)
        nb = ttk.Notebook(f)
        nb.pack(fill="both", expand=True, padx=6)
        self.nb = nb
        self.tabs = {"home": HomeTab(nb, self), "combat": CombatTab(nb, self),
                     "gather": GatherTab(nb, self), "radar": RadarTab(nb, self)}
        for key, title in (("home", "Ana Sayfa"), ("combat", "Combat"), ("gather", "Kaynak Toplama"), ("radar", "Radar Ayarları")):
            nb.add(self.tabs[key], text=f"  {title}  ")
        self._function_bar(f)

    # --- profil çubuğu ---
    def _profile_bar(self, parent):
        bar = ttk.Frame(parent, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Profil:").pack(side="left")
        self.prof_var = tk.StringVar(value=self.profile["name"])
        self.prof_cb = ttk.Combobox(bar, textvariable=self.prof_var, width=24, state="readonly")
        self.prof_cb.pack(side="left", padx=4)
        self.prof_cb.bind("<<ComboboxSelected>>", lambda _e: self.switch_profile(self.prof_var.get()))
        for text, cmd in (("Yeni", self._new_profile), ("Kopyala", self._copy_profile), ("Sil", self._delete_profile)):
            ttk.Button(bar, text=text, width=8, command=cmd).pack(side="left", padx=2)
        self.title_lbl = ttk.Label(bar, foreground="#666")
        self.title_lbl.pack(side="right")
        self._refresh_profiles()

    def _refresh_profiles(self):
        names = self.store.names()
        if self.profile["name"] not in names:
            try:
                self.store.save(self.profile)
            except (ValueError, OSError):
                pass
            names = self.store.names()
        self.prof_cb["values"] = names
        self.prof_var.set(self.profile["name"])

    def switch_profile(self, name):
        self.save_profile()
        self.profile = self.store.load(name)
        self.cfg["hub_profile"] = name
        self.walker.reset([])
        for t in self.tabs.values():
            if hasattr(t, "on_profile_changed"):
                t.on_profile_changed()

    def _ask_name(self, title, initial=""):
        d = tk.Toplevel(self.root)
        d.title(title)
        d.transient(self.root)
        d.grab_set()
        v = tk.StringVar(value=initial)
        ttk.Label(d, text="Ad:").grid(row=0, column=0, padx=8, pady=8)
        e = ttk.Entry(d, textvariable=v, width=28)
        e.grid(row=0, column=1, padx=8)
        e.focus_set()
        out = []
        e.bind("<Return>", lambda _e: (out.append(v.get()), d.destroy()))
        ttk.Button(d, text="Tamam", command=lambda: (out.append(v.get()), d.destroy())).grid(row=1, columnspan=2, pady=8)
        d.wait_window()
        return out[0].strip() if out else None

    def _new_profile(self):
        name = self._ask_name("Yeni profil")
        if name:
            self._create(profiles.blank(name))

    def _copy_profile(self):
        name = self._ask_name("Profili kopyala", self.profile["name"] + " 2")
        if name:
            import copy
            p = copy.deepcopy(self.profile)
            p["name"] = name
            self._create(p)

    def _create(self, prof):
        try:
            self.save_profile()
            self.store.save(prof)
        except (ValueError, OSError) as ex:
            return messagebox.showerror("Profil", str(ex), parent=self.root)
        self.switch_profile(prof["name"])
        self._refresh_profiles()

    def _delete_profile(self):
        if len(self.store.names()) <= 1:
            return messagebox.showinfo("Profil", "Son profil silinemez.", parent=self.root)
        if messagebox.askyesno("Profil sil", f"'{self.profile['name']}' silinsin mi? Rotaları da gider.", parent=self.root):
            self.store.delete(self.profile["name"])
            rest = self.store.names()
            self.profile = self.store.load(rest[0])
            self.switch_profile(rest[0])
            self._refresh_profiles()

    def save_profile(self):
        try:
            self.store.save(self.profile)
        except (ValueError, OSError):
            pass

    # --- fonksiyon çubuğu ---
    def _function_bar(self, parent):
        bar = ttk.Frame(parent, padding=(8, 6))
        bar.pack(fill="x", side="bottom")
        ttk.Separator(parent).pack(fill="x", side="bottom")
        self.fn_buttons = {}

        def btn(key, text, cmd):
            b = ttk.Button(bar, text=text, command=cmd)
            b.pack(side="left", padx=3)
            self.fn_buttons[key] = b
            return b

        btn("center", "Beni ortala", lambda: [t.map.center_on_player() for t in self.tabs.values() if hasattr(t, "map")])
        btn("overlay", "Radar yer paylaşımı", self.toggle_overlay)
        btn("find", "Karakteri yeniden bul", self.rebind)
        ttk.Checkbutton(bar, text="Asistan (öneri)", variable=self.assist_on, style="Toolbutton",
                        command=self._assist_toggled).pack(side="left", padx=8)
        btn("ents", "Varlıklar tablosu", self.open_entities)
        btn("export", "Tanı dışa aktar", self.export_diag)
        self.status = ttk.Label(bar, text="", foreground="#444", anchor="e")
        self.status.pack(side="right", fill="x", expand=True)

    def open_entities(self):
        from .entwin import EntitiesWindow
        EntitiesWindow(self.ov)

    def export_diag(self):
        p = entities.export_samples(self.state.entities, self.labels, pos=self.state.pos)
        self.db.flush(force=True)
        fd = self.write_flow_diag(p.with_name("flow_diag.json"))
        messagebox.showinfo("Tanı", f"Yazıldı:\n{p}\n{p.with_name('spawn_log.json')}\n{fd}", parent=self.root)

    def write_flow_diag(self, path):
        """Konum/paket akışı sorunlarını incelemek için: sayaçlar, son hata, akış durumları, konum kaynakları."""
        import json
        snap = self.state.snapshot()
        dec = getattr(self.cap, "decoder", None)
        flows = []
        if dec is not None:
            for k, f in dec.flows.items():
                flows.append({"flow": [str(x) for x in k], "gaps": f.gaps, "resync": f.resync, "pending": len(f.pending),
                              "buffered_bytes": len(f.buf), "stall_s": None if f.stall_since is None else round(time.time() - f.stall_since, 1)})
        out = {"diag": dict(self.state.diag), "last_decode_error": self.state.last_decode_error,
               "packets": snap.packets, "last_packet_age_s": snap.last_packet_age, "pos_age_s": snap.pos_age,
               "player_id": snap.player_id, "connected": snap.connected, "known_ops": len(dec.known_ops) if dec else None,
               "flows": flows, "capture_alive": bool(self.cap and self.cap.is_alive())}
        try:
            Path(path).write_text(json.dumps(out, indent=1), "utf-8")
        except OSError:
            pass
        return path

    def rebind(self):
        d = tk.Toplevel(self.root)
        d.title("Karakteri yeniden bul")
        d.transient(self.root)
        FindPanel(d, self, on_done=d.destroy, standalone=False).pack(fill="both", expand=True)
        d.geometry("560x430")

    # --- asistan ---
    def _assist_toggled(self):
        if not self.assist_on.get():
            self.assist_text, self.assist_target = "", None
        else:
            self.walker.reset(self.active_route_points())

    def active_route_points(self):
        name = self.profile.get("active_route", "")
        return list(self.profile["routes"].get(name, []))

    def _assist(self):
        """Sıradaki hedef önerisi: aktif rota varsa onun durağı; yoksa seçili kaynakların en yakını."""
        snap = self.state.snapshot()
        if not snap.pos:
            self.assist_text, self.assist_target = "Asistan: konum bekleniyor", None
            return
        pos_m = (snap.pos[0] / 100, snap.pos[1] / 100)
        if self.walker.pts:
            t = self.walker.target(pos_m)
            if t is None:
                self.assist_text, self.assist_target = "Asistan: rota bitti", None
                return
            p, dist, ang = t
            dz = ""
            self.assist_target = (p["x_m"], p["y_m"])
            self.assist_text = f"Asistan: durak {self.walker.i + 1}/{len(self.walker.pts)} {p.get('label', '')} · {dist:.0f} m {entities.compass(ang)}{dz}"
            return
        names = set(self.profile["gather"]["resources"])
        radius = float(self.profile["gather"].get("radius_m", 150.0))
        if not names:
            self.assist_text, self.assist_target = "Asistan: Kaynak Toplama sekmesinde kaynak seç ya da rota etkinleştir", None
            return
        live = [s for s in self.world.view(radius).entities if s.kind == "kaynak" and s.name in names]
        if live:
            s = live[0]
            self.assist_target = (s.x_m, s.y_m)
            self.assist_text = f"Asistan: {s.name} · {s.dist_m:.0f} m {s.compass} · {'+' if s.dz_m >= 0 else ''}{s.dz_m:.0f} m yükseklik"
            return
        ents = catalog.build(self.labels, self.db, "kaynak")
        pts = self.db.points(catalog.ids_for(ents, names))
        if pts:
            best = min(pts, key=lambda q: math.hypot(q[0] / 100 - pos_m[0], q[1] / 100 - pos_m[1]))
            d = math.hypot(best[0] / 100 - pos_m[0], best[1] / 100 - pos_m[1])
            self.assist_target = (best[0] / 100, best[1] / 100)
            nm = (self.labels.get(best[4]) or {}).get("name", "kaynak")
            self.assist_text = f"Asistan: en yakın bilinen {nm} noktası · {d:.0f} m {entities.compass(math.atan2(best[0] / 100 - pos_m[0], -(best[1] / 100 - pos_m[1])))}"
        else:
            self.assist_text, self.assist_target = "Asistan: seçili kaynaklar için bilinen nokta yok (bölgede gezdikçe öğrenilir)", None

    # --- döngü ---
    def _tick(self):
        try:
            snap = self.state.snapshot()
            if snap.pos and (not self.trail or math.hypot(self.trail[-1][0] - snap.pos[0], self.trail[-1][1] - snap.pos[1]) > 300):
                self.trail.append((snap.pos[0], snap.pos[1]))
            if self.main is not None:
                if self.assist_on.get():
                    self._assist()
                self._status(snap)
        finally:
            self.root.after(500, self._tick)

    def _status(self, snap):
        if not snap.connected:
            head = "Oyun bekleniyor…"
        elif snap.player_id is None:
            head = "Karakter bağlı değil"
        elif snap.pos is None:
            head = "Konum bekleniyor"
        else:
            head = f"X {snap.pos[0] / 100:.0f}  Y {snap.pos[1] / 100:.0f}  Z {snap.pos[2] / 100:.0f} m"
        extra = f"   ·   {self.assist_text}" if self.assist_on.get() and self.assist_text else ""
        if snap.connected and snap.last_packet_age is not None and snap.last_packet_age > 4:
            extra += f"   ·   ⚠ paket akışı {snap.last_packet_age:.0f} sn yok"
        elif snap.pos_age is not None and snap.pos_age > 10 and snap.pos is not None:
            extra += f"   ·   konum {snap.pos_age:.0f} sn eski"
        d = self.state.diag
        if d.get("gap") or d.get("stall") or d.get("decode_error") or d.get("feed_error"):
            extra += f"   ·   akış: kayıp {d['gap']} · takılma {d['stall']} · hata {d['decode_error'] + d['feed_error']}"
        if d.get("selfid_rebind") or d.get("pos_outlier"):
            extra += f"   ·   kimlik düzeltme {d['selfid_rebind']} · atılan sapma {d['pos_outlier']}"
        if self.error:
            extra += f"   ·   ⚠ {self.error[:60]}"
        try:
            self.status.config(text=head + extra)
        except tk.TclError:
            pass

    def close(self):
        try:
            self.save_profile()
            self.db.flush(force=True)
            settings.save(self.cfg)
        finally:
            if self.cap:
                self.cap.stop()
            self.root.destroy()


# ---------------------------------------------------------------------------
class FindPanel(ttk.Frame):
    """Karakter bulma: zıplama kalibrasyonu ve (isteğe bağlı) ada göre bulma."""

    def __init__(self, parent, hub: Hub, on_done, standalone: bool = True):
        super().__init__(parent, padding=18)
        self.hub, self.on_done, self.standalone = hub, on_done, standalone
        s = hub.state
        ttk.Label(self, text="Karakterini bul", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(self, text="Oyuncunun konumu bilinmeden harita ve asistan çalışmaz.", foreground="#666").pack(anchor="w", pady=(0, 10))
        self.conn = ttk.Label(self, text="")
        self.conn.pack(anchor="w")
        self.phase = tk.Label(self, text="Hazır", font=("Segoe UI", 15, "bold"), bg="#e5e7eb", fg="#111", pady=14)
        self.phase.pack(fill="x", pady=10)
        self.btn = ttk.Button(self, text="Karakterimi bul  (3 sn dur, 6 sn zıpla / dash / kanat)", command=self._calib)
        self.btn.pack(fill="x", pady=2)
        ttk.Separator(self).pack(fill="x", pady=12)
        ttk.Label(self, text="Ada göre bul (isteğe bağlı)").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=4)
        self.name = tk.StringVar(value=hub.cfg["char_name"])
        ttk.Entry(row, textvariable=self.name, width=26).pack(side="left")
        ttk.Button(row, text="Ada göre bul", command=self._by_name).pack(side="left", padx=6)
        self.hint = ttk.Label(self, text="Ad, karakterin görüş alanına (yeniden) girerken okunur: bölge/kanal değiştirince ya da "
                                         "yakınına yeni biri gelince.", foreground="#666", wraplength=500, justify="left")
        self.hint.pack(anchor="w")
        self.msg = ttk.Label(self, text="", wraplength=500, justify="left")
        self.msg.pack(anchor="w", pady=8)
        ttk.Button(self, text="Konum olmadan devam et" if standalone else "Kapat", command=on_done).pack(anchor="e", side="bottom")
        self._waiting_name = 0.0
        self._poll()

    def _calib(self):
        s = self.hub.state
        s.player_id = None
        s.locked = False
        s.start_calibration()
        self.hub.ov.msg_until = 0.0
        self.msg.config(text="", foreground="#000")

    def _by_name(self):
        name = self.name.get().strip()
        self.hub.cfg["char_name"] = name
        s = self.hub.state
        s.char_name = name
        if not name:
            return self.msg.config(text="Ad boş.", foreground="#b91c1c")
        for eid, pn in s.player_names.items():
            if pn.lower() == name.lower():
                s.set_player_id(eid, lock=True)
                return
        self._waiting_name = time.time() + 120
        self.msg.config(text=f"'{name}' bekleniyor… Bölge/kanal değiştir ya da karakterinin yeniden görünmesini sağla.", foreground="#444")

    def _poll(self):
        if not self.winfo_exists():
            return
        hub, s = self.hub, self.hub.state
        snap = s.snapshot()
        self.conn.config(text="● Oyun bağlı" if snap.connected else "○ Oyun bekleniyor (oyunu aç; Npcap kurulu olmalı)",
                         foreground="#16a34a" if snap.connected else "#b45309")
        if s.calibrating:
            base = s.calib_phase == "base"
            left = (s.calib_base_until if base else s.calib_until) - time.time()
            self.phase.config(text=f"DUR, HAREKET ETME   {max(0, left):.0f} sn" if base else f"ZIPLA / DASH / KANAT!   {max(0, left):.0f} sn",
                              bg="#38bdf8" if base else "#f59e0b")
            self.btn.state(["disabled"])
        else:
            self.btn.state(["!disabled"])
            ov = hub.ov
            if time.time() < ov.msg_until and ov.msg:
                self.msg.config(text=ov.msg, foreground="#16a34a" if ov.msg_ok else "#b91c1c")
            if snap.player_id is not None:
                self.phase.config(text="✔ Karakter bulundu", bg="#86efac")
                if snap.pos is not None:
                    self.after(600, self.on_done)
                    return
            else:
                self.phase.config(text="Hazır", bg="#e5e7eb")
        if self._waiting_name and time.time() > self._waiting_name and snap.player_id is None:
            self._waiting_name = 0.0
            self.msg.config(text="Ad bulunamadı. 'Karakterimi bul' ile devam edebilirsin.", foreground="#b91c1c")
        self.after(200, self._poll)


# ---------------------------------------------------------------------------
class ListPanel(ttk.Frame):
    """Aranabilir, kaydırılabilir, çoklu seçimli liste: seçilenlerin spawn noktaları haritada işaretlenir."""

    def __init__(self, parent, hub: Hub, kind: str, title: str, selected: set, with_unlabeled: bool = False, on_change=None):
        super().__init__(parent, padding=4)
        self.hub, self.kind, self.selected, self.with_unlabeled = hub, kind, selected, with_unlabeled
        self.on_change = on_change
        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.q = tk.StringVar()
        e = ttk.Entry(self, textvariable=self.q)
        e.pack(fill="x", pady=4)
        self.q.trace_add("write", lambda *_: self.refresh(force=True))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.lb = tk.Listbox(box, selectmode="extended", exportselection=False, activestyle="none", width=26)
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", self._on_select)
        self.lb.bind("<Double-1>", self._edit)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Tümünü seç", command=self._all).pack(side="left")
        ttk.Button(row, text="Temizle", command=self._none).pack(side="left", padx=4)
        self.count = ttk.Label(self, foreground="#666")
        self.count.pack(anchor="w")
        if with_unlabeled:
            self.unl = tk.BooleanVar(value=True)
            ttk.Checkbutton(self, text="Etiketsizleri de göster", variable=self.unl,
                            command=lambda: self.refresh(force=True)).pack(anchor="w")
        self._sig = None
        self.entries: list = []
        self.visible: list = []
        self.refresh(force=True)
        self.after(2000, self._auto)

    def _all_entries(self):
        ents = catalog.build(self.hub.labels, self.hub.db, self.kind)
        if self.with_unlabeled and self.unl.get():
            ents += catalog.build(self.hub.labels, self.hub.db, "")
        return ents

    def refresh(self, force: bool = False):
        ents = self._all_entries()
        text = self.q.get()
        sig = (tuple((e.name, e.spawns) for e in ents), text)
        if sig == self._sig and not force:
            return
        self._sig = sig
        self.entries = ents
        self.visible = catalog.filter_entries(ents, text)
        top = self.lb.yview()[0]
        self.lb.delete(0, "end")
        for i, e in enumerate(self.visible):
            self.lb.insert("end", f"{e.name}   ({e.spawns})")
            if e.name in self.selected:
                self.lb.selection_set(i)
        self.lb.yview_moveto(top)
        self._count()

    def _auto(self):
        if self.winfo_exists():
            self.refresh()
            self.after(2000, self._auto)

    def _on_select(self, _e=None):
        sel = set(self.lb.curselection())
        for i, e in enumerate(self.visible):
            (self.selected.add if i in sel else self.selected.discard)(e.name)
        self._count()
        if self.on_change:
            self.on_change()

    def _all(self):
        self.lb.selection_set(0, "end")
        self._on_select()

    def _none(self):
        self.lb.selection_clear(0, "end")
        self._on_select()

    def _count(self):
        n = sum(1 for e in self.entries if e.name in self.selected)
        pts = sum(e.spawns for e in self.entries if e.name in self.selected)
        self.count.config(text=f"{len(self.visible)}/{len(self.entries)} tür · {n} seçili · {pts} nokta")

    def _edit(self, _e=None):
        sel = self.lb.curselection()
        if not sel:
            return
        ent = self.visible[sel[0]]
        d = tk.Toplevel(self)
        d.title("Etiket")
        d.transient(self.winfo_toplevel())
        d.grab_set()
        name = tk.StringVar(value="" if ent.kind == "" else ent.name)
        kind = tk.StringVar(value=ent.kind or self.kind)
        ttk.Label(d, text=f"NPC kimlik: {', '.join(map(str, ent.npc_ids))}").grid(row=0, columnspan=2, padx=8, pady=6)
        ttk.Label(d, text="Ad").grid(row=1, column=0, padx=8, sticky="w")
        ttk.Entry(d, textvariable=name, width=28).grid(row=1, column=1, padx=8)
        ttk.Label(d, text="Tür").grid(row=2, column=0, padx=8, sticky="w")
        ttk.Combobox(d, textvariable=kind, values=KINDS_UI, state="readonly", width=12).grid(row=2, column=1, padx=8, pady=6, sticky="w")

        def ok():
            try:
                for n in ent.npc_ids:
                    self.hub.labels.set(n, name.get(), kind.get())
            except ValueError as ex:
                return messagebox.showerror("Geçersiz", str(ex), parent=d)
            self.selected.discard(ent.name)
            d.destroy()
            for t in self.hub.tabs.values():
                if hasattr(t, "refresh_lists"):
                    t.refresh_lists()

        ttk.Button(d, text="Kaydet", command=ok).grid(row=3, columnspan=2, pady=8)


class HomeTab(ttk.Frame):
    """Ana Sayfa: solda canavar, ortada harita, sağda kaynak spawn arama bölümleri."""

    def __init__(self, parent, hub: Hub):
        super().__init__(parent)
        self.hub = hub
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        self.mon = ListPanel(pw, hub, "canavar", "Canavar spawn lokasyonları", hub.sel_monsters, with_unlabeled=True)
        mid = ttk.Frame(pw)
        self.res = ListPanel(pw, hub, "kaynak", "Kaynak spawn lokasyonları", hub.sel_resources)
        bar = ttk.Frame(mid, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Canlı varlıklar", variable=hub.show_live).pack(side="left")
        self.sel_lbl = ttk.Label(bar, foreground="#666")
        self.sel_lbl.pack(side="right")
        self.map = MapView(mid, hub, self.layers)
        self.map.pack(fill="both", expand=True)
        pw.add(self.mon, weight=0)
        pw.add(mid, weight=1)
        pw.add(self.res, weight=0)

    def refresh_lists(self):
        self.mon.refresh(force=True)
        self.res.refresh(force=True)

    def _points(self, panel: ListPanel, selected: set):
        hub = self.hub
        out = []
        for e in panel.entries:
            if e.name not in selected:
                continue
            for x, y, z, n, npc in hub.db.points(e.npc_ids):
                out.append((x, y, n, e.name))
        return out

    def layers(self):
        hub = self.hub
        live = [s for s in hub.world.view().entities if s.kind in ("canavar", "kaynak", "")][:300]
        route = hub.active_route_points()
        self.sel_lbl.config(text=f"{len(hub.sel_monsters)} canavar · {len(hub.sel_resources)} kaynak türü işaretli")
        return {"monsters": self._points(self.mon, hub.sel_monsters), "resources": self._points(self.res, hub.sel_resources),
                "live": live, "route": route, "route_i": hub.walker.i if hub.assist_on.get() else -1,
                "areas": hub.profile["farm_areas"], "target": hub.assist_target if hub.assist_on.get() else None}


def main():
    root = tk.Tk()
    Hub(root)
    root.mainloop()
