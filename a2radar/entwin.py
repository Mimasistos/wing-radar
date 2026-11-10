"""Varlıklar penceresi: yakındaki canavar/NPC türleri (npc kimliğine göre), adet, en yakın mesafe/yön; kimlik etiketleme."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import entities as E
from . import schemascan


class EntitiesWindow(tk.Toplevel):
    COLS = (("npc", "NPC kimliği", 90), ("ad", "Etiket", 160), ("tur", "Tür", 70), ("adet", "Adet", 50),
            ("yakin", "En yakın", 70), ("yon", "Yön", 45), ("yuk", "Yükseklik", 70))

    def __init__(self, ov):
        super().__init__(ov.root)
        self.ov = ov
        self.title("Varlıklar")
        self.attributes("-topmost", True)
        self.geometry("640x460")
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Yarıçap (m):").pack(side="left")
        self.radius = tk.StringVar(value="200")
        ttk.Spinbox(top, from_=20, to=2000, increment=20, width=6, textvariable=self.radius).pack(side="left", padx=(2, 10))
        self.only_unlabeled = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Yalnız etiketsiz", variable=self.only_unlabeled).pack(side="left")
        self.kind = tk.StringVar(value="hepsi")
        ttk.Combobox(top, textvariable=self.kind, values=("hepsi",) + E.KINDS, state="readonly", width=9).pack(side="left", padx=10)
        self.summary = ttk.Label(top, foreground="#666")
        self.summary.pack(side="right")

        self.tree = ttk.Treeview(self, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse")
        for key, head, w in self.COLS:
            self.tree.heading(key, text=head)
            self.tree.column(key, width=w, anchor="w" if key == "ad" else "center")
        self.tree.pack(fill="both", expand=True, padx=6)
        self.tree.bind("<Double-1>", lambda _: self._label())

        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Etiketle…", command=self._label).pack(side="left")
        ttk.Button(bar, text="Etiketi sil", command=self._unlabel).pack(side="left", padx=6)
        ttk.Button(bar, text="Ham kaydı dışa aktar", command=self._export).pack(side="right")
        ttk.Button(bar, text="Şema taraması (90 sn)", command=self._scan).pack(side="right", padx=6)
        self.note = ttk.Label(self, foreground="#666", wraplength=620, justify="left")
        self.note.pack(fill="x", padx=6, pady=(0, 6))
        self._refresh()

    # --- veri ---
    def _rows(self):
        snap = self.ov.s.snapshot()
        try:
            radius = float(self.radius.get().replace(",", "."))
        except ValueError:
            radius = 200.0
        pid = self.ov.s.player_id
        own = snap.pos
        ents = []
        for e in self.ov.s.entities.snapshot():
            if e.eid == pid or e.npc_id == -1:   # -1: oyuncu (13889 oyuncu biçimi)
                continue
            # kimliği henüz bağlanmamışsa kendi karakterim npc 0 olarak görünür: konumu bire bir aynı olanı at
            if own and e.npc_id == 0 and abs(e.x - own[0]) < 150 and abs(e.y - own[1]) < 150:
                continue
            ents.append(e)
        groups = E.group_entities(ents, snap.pos, radius if snap.pos else None)
        rows = []
        for g in groups:
            lab = self.ov.labels.get(g["npc_id"])
            if self.only_unlabeled.get() and lab:
                continue
            if self.kind.get() != "hepsi" and (not lab or lab["kind"] != self.kind.get()):
                continue
            rows.append((g, lab))
        return snap, rows

    def _refresh(self):
        if not self.winfo_exists():
            return
        snap, rows = self._rows()
        sel = self.tree.selection()
        keep = sel[0] if sel else None
        self.tree.delete(*self.tree.get_children())
        for g, lab in rows:
            near = "-" if g["nearest_m"] is None else f"{g['nearest_m']:.0f} m"
            yon = "-" if g["bearing"] is None else E.compass(g["bearing"])
            yuk = "-" if g["dz_m"] is None else f"{g['dz_m']:+.0f} m"
            self.tree.insert("", "end", iid=str(g["npc_id"]),
                             values=(g["npc_id"], lab["name"] if lab else "", lab["kind"] if lab else "",
                                     g["count"], near, yon, yuk))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        pos = f"konum {snap.pos[0] / 100:.0f}, {snap.pos[1] / 100:.0f}, {snap.pos[2] / 100:.0f} m" if snap.pos else "konum yok"
        total = len(self.ov.s.entities.snapshot())
        self.summary.config(text=f"{total} varlık izleniyor · {len(rows)} tür · {pos}")
        if total == 0:
            self.note.config(text="Henüz varlık görülmedi. Radar bağlı mı? Spawn paket şeması NPC'ler için doğrulanmadı; hiç varlık gelmiyorsa "
                                  "'Ham kaydı dışa aktar' boş çıkar ve bana bildirmelisin.")
        else:
            self.note.config(text="Bir satıra çift tıkla ve ne olduğunu yaz (ör. 'Orman kurdu'). Yön: K=kuzey (oyun -y), mesafe yataydır. "
                                  "Aynı canavar türünün tüm örnekleri aynı NPC kimliğini taşır.")
        self.after(1000, self._refresh)

    # --- etiketleme ---
    def _selected_npc(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _label(self):
        npc = self._selected_npc()
        if npc is None:
            return
        cur = self.ov.labels.get(npc) or {"name": "", "kind": "canavar"}
        d = tk.Toplevel(self)
        d.title(f"NPC {npc}")
        d.attributes("-topmost", True)
        d.transient(self)
        name, kind = tk.StringVar(value=cur["name"]), tk.StringVar(value=cur["kind"])
        ttk.Label(d, text="Ad").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        e = ttk.Entry(d, textvariable=name, width=30)
        e.grid(row=0, column=1, padx=8)
        e.focus_set()
        ttk.Label(d, text="Tür").grid(row=1, column=0, padx=8, sticky="w")
        ttk.Combobox(d, textvariable=kind, values=E.KINDS, state="readonly", width=12).grid(row=1, column=1, padx=8, pady=6, sticky="w")

        def ok(_=None):
            try:
                self.ov.labels.set(npc, name.get(), kind.get())
            except ValueError as ex:
                return messagebox.showerror("Geçersiz", str(ex), parent=d)
            d.destroy()

        e.bind("<Return>", ok)
        ttk.Button(d, text="Kaydet", command=ok).grid(row=2, columnspan=2, pady=8)

    def _unlabel(self):
        npc = self._selected_npc()
        if npc is not None:
            self.ov.labels.remove(npc)

    def _scan(self):
        s = self.ov.s
        if s.pos is None:
            return messagebox.showwarning("Tarama", "Önce konumun bilinmeli (kimlik bağlı olmalı).", parent=self)
        s.scan = schemascan.SchemaScan(90.0)
        self.note.config(text="TARAMA SÜRÜYOR (90 sn): canavarların spawn paketini yakalamak için 30-40 m uzaklaş ve geri gel "
                              "(canavarlar yeniden yüklensin), ya da bölge/kanal değiştir. Bitince dosya yazılır.")
        self.after(91000, self._scan_done)

    def _scan_done(self):
        sc = self.ov.s.scan
        if sc is None or not self.winfo_exists():
            return
        self.ov.s.scan = None
        p = sc.write()
        messagebox.showinfo("Tarama bitti", f"{sc.packets} paket tarandı.\nDosyayı gönder:\n{p}", parent=self)

    def _export(self):
        p = E.export_samples(self.ov.s.entities, self.ov.labels, pos=self.ov.s.pos)
        messagebox.showinfo("Dışa aktarıldı", f"{len(self.ov.s.entities.samples)} NPC türünün ham spawn kaydı yazıldı:\n{p}", parent=self)
