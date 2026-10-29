"""Animated GIFs for the README, recorded from a running instance with sample data.

    python tools/readme_gifs.py --url http://localhost:8757 --out docs/images
    python tools/readme_gifs.py --url <instance with an API key> --answer   # PAID

Without `--answer` it is free: keyword search only, and any request to
/api/ask is aborted. `--answer` also records one AI answer -- exactly one
/api/ask call, on Haiku (~$0.01), and only if the instance has an API key; a
test box has no network, so that one runs against a dev instance on the PC.
The sample manuals are invented text, so the GIFs show no real manual content.

Frames are Playwright screenshots, encoded with Pillow (no ffmpeg needed).
"""
from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

CAR = "2005 E500 4matic"
SEARCH = "tightening torques front axle"
QUESTION = "What are the torques for the wheel bolts and the track rod end?"
ANSWER_MODEL = "claude-haiku-4-5"


class Recorder:
    """Screenshots with how long each stays on screen; identical frames merge."""

    def __init__(self, page, width):
        self.page, self.width, self.frames = page, width, []

    def snap(self, ms=120):
        img = Image.open(io.BytesIO(self.page.screenshot())).convert("RGB")
        if img.width > self.width:
            img = img.resize((self.width, round(img.height * self.width / img.width)),
                             Image.LANCZOS)
        if self.frames and self.frames[-1][0].tobytes() == img.tobytes():
            self.frames[-1][1] += ms
        else:
            self.frames.append([img, ms])

    def type(self, selector, text, every=2):
        self.page.click(selector)
        for i, ch in enumerate(text):
            self.page.keyboard.type(ch)
            if i % every == every - 1:
                self.snap(60)
        self.snap(400)

    def wait_until(self, done, timeout=240, step_ms=500):
        end = time.time() + timeout
        while not done():
            if time.time() > end:
                raise TimeoutError("the page did not finish in time")
            self.snap(step_ms)
            self.page.wait_for_timeout(step_ms)

    def save(self, path):
        # one shared palette keeps colours steady from frame to frame
        frames = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT,
                             dither=Image.Dither.NONE) for f, _ in self.frames]
        frames[0].save(path, save_all=True, append_images=frames[1:], loop=0,
                       duration=[ms for _, ms in self.frames], optimize=True, disposal=1)
        print(f"wrote {path} ({len(frames)} frames, {path.stat().st_size // 1024} KB)")


def start(page, url, rec):
    page.goto(url)
    expect(page.locator("main")).to_be_visible(timeout=30_000)
    page.mouse.move(0, 0)
    rec.snap(600)
    if not page.is_visible("#carInput"):     # a phone: the car box is in the car sheet
        page.click("#carPill")
        rec.snap(500)
    rec.type("#carInput", CAR)
    expect(page.locator("#carList .opt[data-k]").first).to_be_visible(timeout=10_000)
    rec.snap(700)
    page.press("#carInput", "Enter")
    rec.snap(700)


def search_scene(page, url, rec, open_hit):
    start(page, url, rec)
    page.click("#modeTabs [data-mode=kw]")
    rec.type("#q", SEARCH)
    page.click("#searchBtn")
    expect(page.locator("#outKw .hit").first).to_be_visible(timeout=10_000)
    rec.snap(1500)
    if open_hit is not None:
        page.locator("#outKw .hit").nth(open_hit).click()
        expect(page.locator("#srcout")).not_to_contain_text(
            "Cited pages and diagrams appear here", timeout=10_000)
        page.mouse.move(0, 0)
    rec.snap(4000)


def answer_scene(page, url, rec):
    start(page, url, rec)
    page.click("#modeTabs [data-mode=ai]")
    page.select_option("#model", ANSWER_MODEL)
    rec.type("#q", QUESTION, every=3)
    page.click("#askBtn")
    page.mouse.move(0, 0)
    rec.wait_until(lambda: page.locator("#askBtn").is_enabled()
                   and page.locator("#out .spinner").count() == 0)
    rec.snap(2500)
    # then read down the answer
    for _ in range(6):
        page.evaluate("""() => {             // the answer's own scrolling column
            let el = document.querySelector('#out');
            while (el && el.scrollHeight <= el.clientHeight + 1) el = el.parentElement;
            (el || document.scrollingElement).scrollBy(0, 220);
        }""")
        rec.snap(900)
    rec.snap(3000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8757")
    ap.add_argument("--out", default="docs/images")
    ap.add_argument("--answer", action="store_true", help="also record one PAID AI answer")
    ap.add_argument("--only-answer", action="store_true", help="record only the answer (PAID)")
    ap.add_argument("--executable", help="a Chromium/Edge binary (default: Playwright's)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asks = []

    def guard(route):
        if (args.answer or args.only_answer) and not asks:
            asks.append(1)
            return route.continue_()
        return route.abort()            # never more than the one paid call

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.executable)
        scenes = []
        if not args.only_answer:
            scenes += [("search.gif", (1280, 720), 960, lambda pg, r: search_scene(pg, args.url, r, 1)),
                       ("search-phone.gif", (390, 760), 390, lambda pg, r: search_scene(pg, args.url, r, None))]
        if args.answer or args.only_answer:
            scenes += [("answer.gif", (1280, 800), 960, lambda pg, r: answer_scene(pg, args.url, r))]
        for name, size, width, scene in scenes:
            page = browser.new_page(viewport={"width": size[0], "height": size[1]},
                                    color_scheme="dark")
            page.route("**/api/ask*", guard)
            rec = Recorder(page, width)
            scene(page, rec)
            rec.save(out / name)
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
