"""Sitenin harita ekran görüntülerinden Hidden Cube ikon merkezlerini çıkarır ve altgard_cubes.json yazar.

Kullanım:
    python tools/extract_icons.py shot1.webp shot2.webp ... [--out yol.json] [--check klasör]

Birden çok görüntü verilebilir: tam harita ve/veya yakınlaştırılmış parçalar. Her görüntü:
  1. pembe-kırmızı ikon pikselleri renkten ayrılır, araç çubuğu/düğmeler maskelenir;
  2. ikonlar silinip (inpaint) harita görseliyle (a2radar/data/altgard_map.png) çok ölçekli şablon eşlemesiyle hizalanır
     (ölçek + kaydırma; dönme/yansıma yok);
  3. ayrık bileşenler tek ikon merkezi, büyük (üst üste binmiş) bileşenler alanına göre k-means ile bölünür;
  4. merkezler 0..4096 harita uzayına çevrilir.
Birleştirme: en yakın çekim (en küçük ölçek) önce işlenir ve kapsadığı alanı (kenarlardan içeri çekilmiş) sahiplenir;
daha uzak çekimlerin o alandaki tespitleri atılır. Kenar/araç çubuğu bölgesindeki yarım ikonlar yakın çekimlerden alınmaz.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import cv2
import numpy as np

DATA = Path(__file__).resolve().parents[1] / "a2radar" / "data"
SPACE = 4096.0
BASE_SIDE = 2048


def icon_mask(bgr: np.ndarray) -> np.ndarray:
    a = bgr[..., ::-1].astype(float) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(2), a.min(2)
    d = mx - mn + 1e-9
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    sat = d / (mx + 1e-9)
    return ((h < 18) | (h > 335)) & (sat > 0.35) & (sat < 0.85) & (mx > 0.55) & (r - g > 0.25) & (r - b > 0.18)


def ui_mask(bgr: np.ndarray) -> np.ndarray:
    """Koyu araç çubuğu (sol) ve sağ üst düğme: koyu gri pikseller + çevreleri."""
    h, w = bgr.shape[:2]
    dark = (bgr.max(2) < 95).astype(np.uint8)
    out = np.zeros((h, w), np.uint8)
    out[:430, :75] = dark[:430, :75]
    out[:100, w - 75:] = dark[:100, w - 75:]
    out[:60, :360] = np.maximum(out[:60, :360], dark[:60, :360])     # üst-sol arama kutusu (tam harita görüntüsü)
    return cv2.dilate(out, np.ones((9, 9), np.uint8)) > 0


def load_base() -> np.ndarray:
    g = cv2.imread(str(DATA / "altgard_map.png"), cv2.IMREAD_GRAYSCALE)
    return cv2.resize(g, (BASE_SIDE, BASE_SIDE), interpolation=cv2.INTER_AREA)


def clean_gray(bgr: np.ndarray, icons: np.ndarray, ui: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bad = cv2.dilate((icons | ui).astype(np.uint8), np.ones((7, 7), np.uint8))
    return cv2.inpaint(g, bad, 3, cv2.INPAINT_TELEA)


def _match(base, tpl):
    res = cv2.matchTemplate(base, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def register(base: np.ndarray, g: np.ndarray):
    """Gri görüntü g'yi baz görsele hizalar. -> (skor, f, (bx, by)): baz_xy = bx + f * g_xy (2048 uzayı)."""
    h, w = g.shape
    small = cv2.resize(base, (BASE_SIDE // 2, BASE_SIDE // 2), interpolation=cv2.INTER_AREA)
    best = None
    for f in np.geomspace(0.10, 1.10, 56):
        tw, th = int(round(w * f / 2)), int(round(h * f / 2))     # kaba arama yarı çözünürlükte
        if tw < 40 or th < 40 or tw >= small.shape[1] or th >= small.shape[0]:
            continue
        tpl = cv2.resize(g, (tw, th), interpolation=cv2.INTER_AREA)
        sc, loc = _match(small, tpl)
        if best is None or sc > best[0]:
            best = (sc, f)
    if best is None:
        return 0.0, 1.0, (0, 0)
    f0 = best[1]
    best = None
    for f in np.linspace(f0 * 0.95, f0 * 1.05, 21):
        tw, th = int(round(w * f)), int(round(h * f))
        if tw >= base.shape[1] or th >= base.shape[0]:
            continue
        tpl = cv2.resize(g, (tw, th), interpolation=cv2.INTER_AREA)
        sc, loc = _match(base, tpl)
        if best is None or sc > best[0]:
            best = (sc, f, loc)
    sc, f, (bx, by) = best
    return sc, f, (float(bx), float(by))


def components(mask: np.ndarray):
    k = max(3, int(round(mask.shape[1] / 300)) | 1)
    closed = cv2.erode(cv2.dilate(mask.astype(np.uint8), np.ones((k, k), np.uint8)), np.ones((3, 3), np.uint8)) > 0
    n, lab = cv2.connectedComponents(closed.astype(np.uint8), connectivity=8)
    comps = []
    for i in range(1, n):
        ys, xs = np.nonzero(lab == i)
        comps.append(np.c_[xs, ys].astype(float))
    return comps


def kmeans(p: np.ndarray, k: int, iters: int = 12) -> np.ndarray:
    rng = np.random.default_rng(1)
    c = p[rng.choice(len(p), k, replace=False)]
    for _ in range(iters):
        lab = ((p[:, None, :] - c[None, :, :]) ** 2).sum(-1).argmin(1)
        for i in range(k):
            if (lab == i).any():
                c[i] = p[lab == i].mean(0)
    return c


def centers(comps, single: float) -> np.ndarray:
    out = []
    for p in comps:
        if len(p) < single * 0.25:
            continue
        k = max(1, int(round(len(p) / (single * 0.8))))
        out.extend(kmeans(p, min(k, len(p))) if k > 1 else [p.mean(0)])
    return np.array(out)


def process(path: Path, base: np.ndarray, verbose=True):
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        from PIL import Image          # webp için yedek
        bgr = cv2.cvtColor(np.asarray(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    icons, ui = icon_mask(bgr), ui_mask(bgr)
    score, f, (bx, by) = register(base, clean_gray(bgr, icons, ui))
    comps = components(icons & ~ui)
    small = sorted(len(p) for p in comps if len(p) < 700 * (w / 1836) ** 2)
    single = float(np.median(small)) if small else 180.0
    c = centers(comps, single)
    k = SPACE / BASE_SIDE
    info = {"file": path.name, "score": score, "f": f, "single": single, "size": (w, h),
            "rect": (bx * k, by * k, (bx + w * f) * k, (by + h * f) * k),            # 4096 uzayı
            "px_per_map": 1.0 / (f * k)}                                              # ekran px / harita birimi
    pts = []
    for x, y in c:
        pts.append((x, y, (bx + f * x) * k, (by + f * y) * k))
    if verbose:
        print(f"  {path.name}: skor {score:.3f}, kapsama {info['rect'][2] - info['rect'][0]:.0f}x{info['rect'][3] - info['rect'][1]:.0f} harita-px, "
              f"{len(c)} ikon (tek ikon alanı ~{single:.0f} px)")
    return info, pts, bgr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", type=Path, nargs="+")
    ap.add_argument("--out", type=Path, default=DATA / "altgard_cubes.json")
    ap.add_argument("--check", type=Path, help="Her görüntü için tespit önizlemesi yazılacak klasör")
    ap.add_argument("--min-score", type=float, default=0.45)
    a = ap.parse_args(argv)
    base = load_base()
    results = []
    for p in a.shots:
        info, pts, bgr = process(p, base)
        if info["score"] < a.min_score:
            print(f"  ! {p.name}: hizalama güvenilir değil (skor {info['score']:.2f}), atlandı")
            continue
        results.append((info, pts, bgr))
    if not results:
        sys.exit("Hiçbir görüntü hizalanamadı.")
    results.sort(key=lambda r: r[0]["f"])                                   # en yakın çekim önce
    accepted_rects, final = [], []
    for info, pts, bgr in results:
        h, w = info["size"][1], info["size"][0]
        edge = 26                                                           # yarım ikon payı (ekran px)
        keep = 0
        for x, y, mx, my in pts:
            if not (0 <= mx <= SPACE and 0 <= my <= SPACE):
                continue
            wide = info["f"] > 0.7                                          # tam harita görüntüsü: kenar payı yok
            if not wide and (x < edge or y < edge or x > w - edge or y > h - edge):
                continue
            if any(r[0] <= mx <= r[2] and r[1] <= my <= r[3] for r in accepted_rects):
                continue
            final.append([round(mx, 1), round(my, 1)])
            keep += 1
        # sahiplenilen alan: kenarlardan içeri çekilmiş dikdörtgen (harita birimi)
        inset = edge / info["px_per_map"] if info["f"] <= 0.7 else 0.0
        x0, y0, x1, y1 = info["rect"]
        accepted_rects.append((x0 + inset, y0 + inset, x1 - inset, y1 - inset))
        print(f"  {info['file']}: {keep} ikon eklendi")
        if a.check:
            a.check.mkdir(parents=True, exist_ok=True)
            vis = bgr.copy()
            for x, y, mx, my in pts:
                cv2.circle(vis, (int(x), int(y)), 9, (0, 255, 0), 2)
            cv2.imwrite(str(a.check / f"{Path(info['file']).stem}_check.png"), vis)
    a.out.write_text(json.dumps(final, separators=(",", ":")), "utf-8")
    print(f"Toplam {len(final)} ikon merkezi -> {a.out}")


if __name__ == "__main__":
    main()
