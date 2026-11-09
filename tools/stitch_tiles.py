"""İndirdiğin harita parçalarını (z_x_y.webp) tek bir altgard_map.png'ye birleştirir.

Kullanım:
    python tools/stitch_tiles.py <parça_klasörü> [--zoom N] [--flip-y] [--out yol.png]

Dosya adındaki ilk üç sayı z, x, y kabul edilir (ör. 3_5_2.webp, "3_5_2 (1).webp" de olur).
--zoom verilmezse, eksiksiz (tüm x,y) bulunan en yüksek zoom seçilir; yoksa en çok parçası olan zoom kullanılır.
Eksik parçalar siyah kalır. Çıktı varsayılan olarak a2radar/data/altgard_map.png'dir.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

PAT = re.compile(r"(\d+)_(\d+)_(\d+)")
EXTS = {".webp", ".png", ".jpg", ".jpeg"}


def scan(folder: Path):
    tiles: dict[int, dict[tuple[int, int], Path]] = {}
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in EXTS:
            m = PAT.search(p.stem)
            if m:
                z, x, y = map(int, m.groups())
                tiles.setdefault(z, {}).setdefault((x, y), p)
    return tiles


def pick_zoom(tiles):
    complete = [z for z, t in tiles.items() if len(t) == (2 ** z) ** 2]
    return max(complete) if complete else max(tiles, key=lambda z: len(tiles[z]))


def stitch(tiles_z: dict, zoom: int, flip_y: bool = False) -> Image.Image:
    n = 2 ** zoom
    first = Image.open(next(iter(tiles_z.values())))
    tw, th = first.size
    out = Image.new("RGB", (tw * n, th * n), (0, 0, 0))
    for (x, y), p in tiles_z.items():
        if not (0 <= x < n and 0 <= y < n):
            continue
        im = Image.open(p).convert("RGB")
        if im.size != (tw, th):
            im = im.resize((tw, th))
        out.paste(im, (x * tw, (n - 1 - y if flip_y else y) * th))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--zoom", type=int)
    ap.add_argument("--flip-y", action="store_true")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "a2radar" / "data" / "altgard_map.png")
    a = ap.parse_args(argv)
    tiles = scan(a.folder)
    if not tiles:
        sys.exit("Klasörde z_x_y biçiminde parça bulunamadı.")
    print("Bulunan zoom düzeyleri:", {z: f"{len(t)}/{(2 ** z) ** 2}" for z, t in sorted(tiles.items())})
    z = a.zoom if a.zoom is not None else pick_zoom(tiles)
    if z not in tiles:
        sys.exit(f"Zoom {z} için parça yok.")
    miss = (2 ** z) ** 2 - len(tiles[z])
    img = stitch(tiles[z], z, a.flip_y)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(a.out)
    print(f"Zoom {z}: {len(tiles[z])} parça, {miss} eksik -> {a.out} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
