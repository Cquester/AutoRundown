"""The app's icons, drawn from code -- FREE, no network, ~1 s.

    python tools/make_icons.py

Writes autorundown/web/icons/: favicon.ico (16-256 px, the Windows
shortcuts, the tray and the browser tab), icon-192.png and icon-512.png (the
home-screen icon: manifest.webmanifest), icon-maskable-512.png (the same,
full bleed, for launchers that crop it to a circle), apple-touch-icon.png
(iPhone home screen). icon.svg is the same drawing by hand: change both
together. A hex nut on the amber the tray icon always had -- a mechanic's
mark that stays readable at 16 px.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "autorundown" / "web" / "icons"
AMBER = (245, 166, 35, 255)
INK = (20, 22, 28, 255)
SS = 1024          # drawn this big, then scaled down: smooth edges at 16 px


def draw(size: int = SS, bleed: bool = False) -> Image.Image:
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bleed:
        d.rectangle((0, 0, SS, SS), fill=AMBER)
    else:
        d.rounded_rectangle((0, 0, SS - 1, SS - 1), radius=int(SS * 0.22), fill=AMBER)
    # a flat-top hexagon (the nut) with its hole; smaller when full bleed, so a
    # circular crop keeps it whole
    r = SS * (0.27 if bleed else 0.336)
    c = SS / 2
    d.polygon([(c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a)))
               for a in range(0, 360, 60)], fill=INK)
    h = r * 0.44
    d.ellipse((c - h, c - h, c + h, c + h), fill=AMBER)
    return img.resize((size, size), Image.LANCZOS) if size != SS else img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    main_img = draw()
    main_img.save(OUT / "favicon.ico",
                  sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for name, size, bleed in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                              ("icon-maskable-512.png", 512, True),
                              ("apple-touch-icon.png", 180, True)):   # iOS rounds it itself
        draw(size, bleed).save(OUT / name, optimize=True)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:24} {p.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
