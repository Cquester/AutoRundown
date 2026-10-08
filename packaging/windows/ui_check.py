"""Drive an installed AutoRundown the way a new user does, in a real browser.

    python packaging/windows/ui_check.py --corpus <dir> [--url http://localhost:8756]
                                         [--channel msedge] [--shots <dir>]

Expects a server on an EMPTY index (a fresh install). Checks, in order:
the first-run import dialog opens by itself; browsing to <corpus> detects it;
Import runs the import job (ingest.py in a child process -- the path the
Windows bundle exists to keep working) to "Finished"; the car box finds a
car; keyword search returns hits; a hit opens its page; the layout holds at
phone width. Screenshots of each step go to --shots. Free: never calls /api/ask
(any request to it fails the run).
Dev-only: needs `pip install -r requirements-dev.txt`, not shipped.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8756")
    ap.add_argument("--corpus", required=True, help="folder to import (the sample corpus)")
    ap.add_argument("--car", default="w201 190", help="what to type in the car box")
    ap.add_argument("--query", default="torque")
    ap.add_argument("--channel", default=None, help="msedge / chrome; default: bundled Chromium")
    ap.add_argument("--executable", default=None, help="a browser binary instead of --channel")
    ap.add_argument("--shots", default="ui-shots")
    ap.add_argument("--log-shots", default="",
                    help="comma list of shot names to also print as base64 JPEG between "
                         "'BEGIN SHOT <name>' / 'END SHOT' lines (to read them from a CI log)")
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    corpus = str(Path(args.corpus).resolve())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel, executable_path=args.executable)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
        page.route("**/api/ask*", lambda r: (errors.append("paid /api/ask called"), r.abort()))

        to_log = set(filter(None, args.log_shots.split(",")))

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}.png"))
            print(f"  screenshot {name}.png")
            if name in to_log:
                b64 = base64.b64encode(page.screenshot(type="jpeg", quality=40)).decode()
                print(f"BEGIN SHOT {name}")
                for i in range(0, len(b64), 2000):
                    print(b64[i:i + 2000])
                print("END SHOT", flush=True)

        def step(msg: str) -> None:
            print(f"ok   {msg}", flush=True)

        page.goto(args.url)
        dlg = page.locator("#importDlg")
        expect(dlg).to_be_visible(timeout=15_000)
        expect(page.locator("#impIntro")).to_contain_text("No manuals are indexed yet")
        step("first start: import dialog opened by itself")
        shot("01-first-run")

        page.fill("#impPath", corpus)
        page.press("#impPath", "Enter")
        expect(page.locator("#impDetected")).to_contain_text("Detected:", timeout=30_000)
        step(f"folder detected: {page.locator('#impDetected').inner_text()}")
        shot("02-detected")

        page.click("#impStart")
        status = page.locator("#impStatus")
        expect(status).to_contain_text("Finished", timeout=180_000)  # "Failed" times out here
        step(f"import job: {status.inner_text()}")
        shot("03-imported")
        log = page.locator("#impLog").inner_text()
        if "indexed" not in log:
            print(log[-2000:])
            raise AssertionError("import log has no 'indexed' line")

        page.click("#impClose")
        page.fill("#carInput", args.car)
        opt = page.locator("#carList .opt[data-k]").first
        expect(opt).to_be_visible(timeout=10_000)
        label = opt.locator("div").first.inner_text()
        page.press("#carInput", "Enter")
        expect(page.locator("#carInput")).to_have_value(label)
        step(f"car box: {args.car!r} -> {label}")

        page.fill("#q", args.query)
        page.click("#searchBtn")
        hits = page.locator("#out .hit")
        expect(hits.first).to_be_visible(timeout=15_000)
        step(f"keyword search {args.query!r}: {hits.count()} hit(s), first: "
             f"{hits.first.locator('.t').inner_text()}")
        shot("04-search")

        hits.first.click()
        expect(page.locator("#srcout")).not_to_contain_text("Cited pages and diagrams appear here",
                                                            timeout=15_000)
        step("result opens its page")
        shot("05-page")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        shot("06-phone")
        if overflow > 1:
            raise AssertionError(f"page scrolls sideways at phone width ({overflow}px)")
        step("phone width: no sideways scroll")

        browser.close()
        if errors:
            print("\n".join(errors))
            return 1
    print("UI check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
