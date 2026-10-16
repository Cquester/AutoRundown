"""Scan VIN: reading a VIN off a photo, in a real browser -- FREE, ~1-2 min.

    python tests/test_vin_scan.py [--n 10] [--headed]

Runs index.html's own vin-check + vin-scan blocks in Chromium (the installed
Edge/Chrome, or Playwright's) with the vendored libraries served from
autorundown/web/vendor/ exactly as /vendor/ serves them -- no server, no
network (every other request is refused). Pictures are generated here
(tests/_vin_images.py): Code 39 (with and without the import "I"), Data
Matrix and QR barcodes, and printed VIN labels, clean and roughened like a
phone photo. No real VIN photo is used or stored.

Checks: every barcode is read exactly; printed text is read exactly in most
cases (the rate is printed, and must stay above a floor); a read VIN is never
searched by itself (the result only fills the box -- that part is in the
phone test, .dev/qa/claude-vin-scan/ui_scan.py); the Tesseract build for
phones without WebAssembly SIMD works too.
"""
from __future__ import annotations

import argparse
import io
import random
import re
import sys
import time

from PIL import Image, ImageDraw

from _paths import ROOT
from _vin_images import _font, barcode, fin, text_label, us_vin

HTML = (ROOT / "autorundown" / "web" / "index.html").read_text(encoding="utf-8")
VENDOR = ROOT / "autorundown" / "web" / "vendor"
TYPES = {".js": "text/javascript", ".wasm": "application/wasm", ".gz": "application/octet-stream"}
ORIGIN = "http://scan.test"
FAILS: list[str] = []
# printed text, read exactly (after the I/O/Q fix): the floor for the two
# sets of generated labels. Measured 2026-10-16 (Edge, --n 10): clean 20/20,
# rough (bold, blurred, noisy, shaded, JPEG 70) 13/20 -- its misses are
# 8/9/6/0 confusions; a US VIN's are caught by its check digit, a FIN's are not
TEXT_FLOOR = {"clean": 0.9, "rough": 0.5}


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL", msg)


def block(name: str) -> str:
    m = re.search(rf"/\* {name}:begin.*?\*/(.*?)/\* {name}:end \*/", HTML, re.S)
    if not m:
        raise SystemExit(f"no {name} block in index.html")
    return m.group(1)


PAGE = """<!doctype html><meta charset="utf-8"><title>scan</title>
<input type="file" id="f"><script>
%s
%s
const isMaker = w => /^(WD[A-Z0-9]|W1[A-Z]|4JG|55S|WME)$/.test(w);   // Mercedes WMIs
window.scanIt = async () => {
  const t0 = performance.now();
  const r = await vinScan(document.querySelector('#f').files[0], {isMaker});
  return {vin: r.best && r.best.vin, raw: r.best && r.best.raw, changed: r.best && r.best.changed,
          check: r.best && r.best.check, unsure: r.best && r.best.unsure, source: r.source, text: r.text, crop: !!r.crop,
          ms: Math.round(performance.now() - t0)};
};
</script>""" % (block("vin-check"), block("vin-scan"))


def serve(route):
    url = route.request.url
    if url in (ORIGIN + "/", ORIGIN + "/index.html"):
        return route.fulfill(status=200, content_type="text/html", body=PAGE)
    if url.startswith(ORIGIN + "/vendor/"):
        f = (VENDOR / url[len(ORIGIN + "/vendor/"):]).resolve()
        if f.is_file() and f.is_relative_to(VENDOR.resolve()):
            return route.fulfill(status=200, content_type=TYPES.get(f.suffix, "application/octet-stream"),
                                 body=f.read_bytes())
    return route.fulfill(status=404, body="no")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="pictures per kind")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    rnd = random.Random(1981)
    from playwright.sync_api import sync_playwright

    cases = []                      # (kind, truth, jpeg bytes)
    for i in range(args.n):
        cases.append(("code39", v := (us_vin(rnd) if i % 2 else fin(rnd)), barcode(v, "code39", rnd, hard=i % 3 == 2)))
        cases.append(("datamatrix", v := us_vin(rnd), barcode(v, "datamatrix", rnd, hard=i % 3 == 2)))
        cases.append(("qr", v := fin(rnd), barcode(v, "qr", rnd, hard=i % 3 == 2)))
    for i in range(args.n * 2):
        v = us_vin(rnd) if i % 2 else fin(rnd)
        cases.append(("text-clean", v, text_label(v, rnd, hard=False, spaced=i % 4 == 3)))
    for i in range(args.n * 2):
        v = us_vin(rnd) if i % 2 else fin(rnd)
        cases.append(("text-rough", v, text_label(v, rnd, hard=True, spaced=i % 4 == 3)))

    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(channel=channel, headless=not args.headed)
                break
            except Exception:
                continue
        if browser is None:
            raise SystemExit("no browser for Playwright")
        page = browser.new_page()
        page.route("**/*", serve)
        page.goto(ORIGIN + "/")
        stats: dict[str, list] = {}
        wrong_ok = 0
        for kind, truth, jpeg in cases:
            page.set_input_files("#f", files=[{"name": "vin.jpg", "mimeType": "image/jpeg", "buffer": jpeg}])
            r = page.evaluate("scanIt()")
            good = r["vin"] == truth
            stats.setdefault(kind, []).append((good, r["ms"]))
            if not good:
                print(f"   miss {kind:11} {truth} -> {r['vin']} (read {r['text']!r:.60}, {r['source']})")
                # a wrong US VIN that still passes its check digit is the bad case
                if r["vin"] and r["check"] == "ok":
                    wrong_ok += 1
            if kind.startswith(("code39", "datamatrix", "qr")) and r["source"] == "printed text (OCR)":
                print(f"   note {kind} read from the text, not the barcode")
            if r["vin"] and not r["crop"]:
                fail(f"{kind} {truth}: no crop shown")
        print()
        for kind, rows in stats.items():
            ok = sum(g for g, _ in rows)
            ms = sorted(m for _, m in rows)
            print(f"   {kind:11} {ok}/{len(rows)} read exactly  (median {ms[len(ms) // 2]} ms, max {ms[-1]} ms)")
            rate = ok / len(rows)
            if kind in ("code39", "datamatrix", "qr") and ok != len(rows):
                fail(f"{kind}: {len(rows) - ok} barcodes not read")
            if kind.startswith("text-") and rate < TEXT_FLOOR[kind[5:]]:
                fail(f"{kind}: {rate:.0%} read exactly, the floor is {TEXT_FLOOR[kind[5:]]:.0%}")
        if wrong_ok:
            fail(f"{wrong_ok} wrong VIN(s) passed the check digit")

        # older phones: the Tesseract build without SIMD
        page2 = browser.new_page()
        page2.route("**/*", serve)
        page2.add_init_script("window.VIN_SCAN_NO_SIMD = true")
        page2.goto(ORIGIN + "/")
        v = us_vin(rnd)
        page2.set_input_files("#f", files=[{"name": "v.jpg", "mimeType": "image/jpeg",
                                            "buffer": text_label(v, rnd)}])
        t0 = time.time()
        r = page2.evaluate("scanIt()")
        print(f"   no-SIMD build: {r['vin']} ({'ok' if r['vin'] == v else 'MISS'}, {time.time() - t0:.1f} s)")
        if r["vin"] != v:
            fail(f"no-SIMD Tesseract build: read {r['vin']}, want {v}")

        # a photo with no VIN in it: nothing goes in the box
        blank = Image.new("RGB", (900, 500), (235, 235, 230))
        ImageDraw.Draw(blank).text((40, 200), "TIRE PRESSURE 2.4 BAR  245/45 R18", fill="black",
                                   font=_font(40))
        buf = io.BytesIO()
        blank.save(buf, "JPEG")
        page.set_input_files("#f", files=[{"name": "n.jpg", "mimeType": "image/jpeg", "buffer": buf.getvalue()}])
        r = page.evaluate("scanIt()")
        if r["vin"]:
            fail(f"a photo with no VIN gave {r['vin']}")
        print(f"   no VIN in the photo: {r['vin']!r}, text read {r['text']!r:.50}")

        # not a picture
        page.set_input_files("#f", files=[{"name": "x.jpg", "mimeType": "image/jpeg", "buffer": b"not a jpeg"}])
        err = page.evaluate("scanIt().then(() => 'no error', e => e.message)")
        if "picture" not in err:
            fail(f"a broken file: {err!r}")
        browser.close()

    print("\nFAILED" if FAILS else "\nALL PASS", f"({len(FAILS)} failures)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
