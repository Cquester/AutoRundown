"""Made-up VIN pictures for the Scan VIN tests: barcodes (Code 39, Data
Matrix, QR) and printed VIN labels, roughened like a phone photo (a tilt,
blur, noise, uneven light, JPEG). Generated on the fly -- no real VIN photo is
ever committed. Needs Pillow and pystrich (requirements-dev.txt)."""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

VAL = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9]))
VAL.update({str(d): d for d in range(10)})
WEIGHT = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def check_digit(vin: str) -> str:
    r = sum(VAL[c] * w for c, w in zip(vin, WEIGHT)) % 11
    return "X" if r == 10 else str(r)


def with_check(vin: str) -> str:
    """The same VIN with a right check digit at position 9."""
    return vin[:8] + check_digit(vin) + vin[9:]


def us_vin(rnd: random.Random) -> str:
    """A made-up US-format Mercedes VIN (WDB/WDD/4JG, coded letters at 4-8,
    model year at 10) with a right check digit."""
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    body = (rnd.choice(["WDB", "WDD", "4JG", "WDC"]) + rnd.choice("BDFGHJKNRSU")
            + rnd.choice("FGHAB") + str(rnd.randint(1, 9)) + str(rnd.randint(0, 9))
            + rnd.choice(letters) + "0" + rnd.choice("YABCDEF123456789")
            + rnd.choice("ABFGHX") + "".join(str(rnd.randint(0, 9)) for _ in range(6)))
    return with_check(body)


def fin(rnd: random.Random) -> str:
    """A made-up European FIN: WDB/WDD/W1K + Baumuster digits + plant + serial."""
    return (rnd.choice(["WDB", "WDD", "W1K", "WDC"]) + rnd.choice(["210", "211", "203", "204", "164"])
            + f"{rnd.randint(0, 299):03d}" + rnd.choice("12") + rnd.choice("AFNX")
            + "".join(str(rnd.randint(0, 9)) for _ in range(6)))


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)          # Pillow >= 10.1: a TrueType font


def _photo(img: Image.Image, rnd: random.Random, *, tilt: float, blur: float, noise: int,
           shade: bool, scale: float, quality: int) -> bytes:
    """Make a clean picture look like a phone photo of it."""
    img = img.convert("RGB")
    bg = Image.new("RGB", (int(img.width * 1.4) + 80, int(img.height * 1.6) + 80),
                   tuple(rnd.randint(150, 215) for _ in range(3)))
    bg.paste(img, ((bg.width - img.width) // 2, (bg.height - img.height) // 2))
    img = bg.rotate(tilt, resample=Image.BICUBIC, expand=True, fillcolor=(170, 170, 170))
    if shade:                                   # light falling off to one side
        grad = Image.linear_gradient("L").rotate(rnd.choice([0, 90, 180, 270])).resize(img.size)
        dark = Image.new("RGB", img.size, (60, 60, 60))
        img = Image.composite(img, dark, grad.point(lambda v: 150 + v * 105 // 255))
    if scale != 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = img.load()
        for _ in range(img.width * img.height // 6):
            x, y = rnd.randrange(img.width), rnd.randrange(img.height)
            r, g, b = px[x, y]
            n = rnd.randint(-noise, noise)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


def barcode(vin: str, kind: str, rnd: random.Random, hard: bool = False) -> bytes:
    """A photo of a VIN barcode label. kind: code39 | datamatrix | qr. A
    Code 39 VIN gets the leading "I" some US labels carry, half the time."""
    from pystrich.code39 import Code39Encoder
    from pystrich.datamatrix import DataMatrixEncoder
    from pystrich.qrcode import QRCodeEncoder
    if kind == "code39":
        text = ("I" if rnd.random() < 0.5 else "") + vin
        code = Code39Encoder(text).get_pilimage(bar_width=rnd.choice([2, 3]))
    elif kind == "datamatrix":
        code = DataMatrixEncoder(vin).get_pilimage(cellsize=rnd.choice([5, 6, 8]))
    else:
        code = QRCodeEncoder(vin).get_pilimage(cellsize=rnd.choice([5, 6, 8]))
    code = code.convert("RGB")
    label = Image.new("RGB", (code.width + 60, code.height + 70), "white")
    label.paste(code, (30, 20))
    ImageDraw.Draw(label).text((30, code.height + 30), "VIN " + vin, fill="black", font=_font(22))
    return _photo(label, rnd, tilt=rnd.uniform(-6, 6) if hard else rnd.uniform(-2, 2),
                  blur=rnd.uniform(0.5, 1.2) if hard else rnd.uniform(0, 0.6),
                  noise=24 if hard else 10, shade=hard, scale=rnd.uniform(0.8, 1.3),
                  quality=70 if hard else 88)


def text_label(vin: str, rnd: random.Random, hard: bool = False, spaced: bool = False) -> bytes:
    """A photo of a printed VIN label: maker lines above and below the VIN,
    the VIN in a plain or bold face, sometimes written in groups."""
    size = rnd.randint(30, 48)
    shown = f"{vin[:3]} {vin[3:9]} {vin[9:]}" if spaced else vin
    lines = [("MERCEDES-BENZ", 26), (f"VIN  {shown}", size),
             (f"GVWR {rnd.randint(2000, 2900)} KG   MADE IN GERMANY", 24)]
    w = 120 + max(int(_font(s).getlength(t)) for t, s in lines)
    img = Image.new("RGB", (w, 60 + sum(s + 26 for _, s in lines)), (250, 250, 246))
    d = ImageDraw.Draw(img)
    y = 30
    for t, s in lines:
        d.text((60, y), t, fill=(20, 20, 20), font=_font(s), stroke_width=1 if (hard and s == size) else 0,
               stroke_fill=(20, 20, 20))
        y += s + 26
    return _photo(img, rnd, tilt=rnd.uniform(-4, 4) if hard else rnd.uniform(-1.5, 1.5),
                  blur=rnd.uniform(0.6, 1.3) if hard else rnd.uniform(0, 0.6),
                  noise=28 if hard else 12, shade=hard, scale=rnd.uniform(0.9, 1.4),
                  quality=70 if hard else 88)
