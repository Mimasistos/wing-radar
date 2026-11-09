"""Harita arka plan görseli (isteğe bağlı). Kullanıcı kaydeder; yoksa her şey düz zeminde çizilir.

Aranan dosya: a2radar/data/altgard_map.(png|jpg|jpeg|webp) veya %APPDATA%/A2Radar/ altında aynı ad.
Görsel kare kabul edilir ve 0..4096 harita uzayına gerilir (işaretçi koordinatlarıyla aynı uzay).
"""
from __future__ import annotations

import os
from pathlib import Path

SPACE = 4096.0
NAMES = [f"altgard_map.{e}" for e in ("png", "jpg", "jpeg", "webp")]
MAX_SIDE = 2048


def candidates():
    here = Path(__file__).parent / "data"
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "A2Radar"
    return [d / n for d in (here, appdata) for n in NAMES]


def find():
    return next((p for p in candidates() if p.is_file()), None)


class MapImage:
    def __init__(self, path):
        from PIL import Image
        im = Image.open(path).convert("RGB")
        side = min(MAX_SIDE, max(im.size))
        self.im = im.resize((side, side), Image.BILINEAR)   # kareye ger
        self.n = side
        self.path = Path(path)

    def full(self, size: int, dim: float = 0.0):
        """Tüm harita size x size; dim: siyaha doğru karartma (0..1)."""
        from PIL import Image
        out = self.im.resize((size, size), Image.BILINEAR)
        return Image.blend(out, Image.new("RGB", out.size, (0, 0, 0)), dim) if dim else out

    def crop(self, cx, cy, hx, hy, out_w, out_h, alpha: int = 255):
        """Harita uzayında merkez (cx, cy), yarım genişlik/yükseklik (hx, hy) -> RGBA out_w x out_h."""
        from PIL import Image
        k = self.n / SPACE
        box = (round((cx - hx) * k), round((cy - hy) * k), round((cx + hx) * k), round((cy + hy) * k))
        out = self.im.crop(box).resize((max(1, out_w), max(1, out_h)), Image.BILINEAR).convert("RGBA")
        if alpha < 255:
            out.putalpha(alpha)
        return out


def _warp(self, coeffs, out_w, out_h, alpha=255):
    from PIL import Image
    out = self.im.transform((max(1, out_w), max(1, out_h)), Image.AFFINE, coeffs, Image.BILINEAR).convert("RGBA")
    if alpha < 255:
        out.putalpha(alpha)
    return out


MapImage.warp = _warp


def _land_inside(self, margin_px: float = 36.0):
    """Kara maskesi: harita uzayında (mx, my) -> kara (veya kıyıdan margin_px içinde) mi?

    Kara = kırmızı/yeşil baskın ve doygun ya da koyu pikseller; deniz mavimsi, parşömen açık krem.
    512x512'de hesaplanır, kıyıya margin_px pay verilir. Harita dışındaki noktalar kara sayılmaz.
    """
    from PIL import Image, ImageFilter
    G = 512
    sm = self.im.resize((G, G), Image.BILINEAR)
    px = sm.load()
    mask = Image.new("L", (G, G), 0)
    mp = mask.load()
    for y in range(G):
        for x in range(G):
            r, g, b = px[x, y]
            mx_, mn_ = max(r, g, b), min(r, g, b)
            val, sat = mx_ / 255, (mx_ - mn_) / (mx_ + 1)
            if (r - b > 38 and sat > 0.22) or (val < 0.52 and not (b > r + 8)):
                mp[x, y] = 255
    clean = mask.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MaxFilter(3))
    size = 2 * max(1, round(margin_px * G / SPACE)) + 1
    dil = clean.filter(ImageFilter.MaxFilter(size)).load()
    k = G / SPACE

    def inside(mx, my):
        if not (0 <= mx < SPACE and 0 <= my < SPACE):
            return False
        return dil[min(G - 1, int(mx * k)), min(G - 1, int(my * k))] > 0

    return inside


MapImage.land_inside = _land_inside


def load():
    p = find()
    if not p:
        return None
    try:
        return MapImage(p)
    except Exception:
        return None
