"""Drive an installed AutoRundown the way a new user does, in a real browser.

    python packaging/windows/ui_check.py --corpus <dir> [--url http://localhost:8756]
                                         [--channel msedge] [--shots <dir>]

Expects a server on an EMPTY index (a fresh install). Checks, in order:
the first-run "Set up AutoRundown" checklist opens by itself; its "Add
manuals" step opens Shop setup > Manuals; browsing to <corpus> detects it;
Import runs the import job (ingest.py in a child process -- the path the
Windows bundle exists to keep working) to "Finished"; the car box finds a
car; keyword search returns hits; a hit opens its page; the layout holds at
phone width. Screenshots of each step go to --shots. Free: never calls /api/ask
(any request to it fails the run).
Dev-only: needs `pip install -r requirements-dev.txt`, not shipped.
"""
from __future__ import annotations

import argparse
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

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}.png"))
            print(f"  screenshot {name}.png")

        def step(msg: str) -> None:
            print(f"ok   {msg}", flush=True)

        page.goto(args.url)
        expect(page.locator("#sec-welcome")).to_be_visible(timeout=30_000)
        expect(page.locator("#sec-welcome")).to_contain_text("Set up AutoRundown")
        if page.locator("main").is_visible():
            raise AssertionError("the search screen shows before any manual is imported")
        step("first start: the set-up checklist opened by itself")
        shot("01-first-run")

        page.click("#wManuals a")                  # "Add manuals"
        expect(page.locator("#sec-manuals")).to_be_visible(timeout=10_000)
        page.click(".srctabs [data-src=folder]")   # Manuals opens on the LEMON tab
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

        page.click("#navBack")                     # "Back to search", shown once manuals exist
        expect(page.locator("main")).to_be_visible(timeout=10_000)
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

        page.click("#settingsBtn")                 # Shop setup: a page, not a pop-up
        expect(page.locator("#setup")).to_be_visible(timeout=10_000)
        page.click("a[data-sec=phones]")
        expect(page.locator("#sPhone")).to_be_visible()
        step(f"shop setup: {page.locator('#sPhone').inner_text()}")
        start_row = page.locator("#sStartRow").is_visible()
        if start_row != (sys.platform == "win32"):
            raise AssertionError(f"'Start when I sign in' shown={start_row} on {sys.platform}")
        step(f"shop setup: start-at-sign-in option {'shown' if start_row else 'hidden (not Windows)'}")
        shot("06-settings")
        page.click("#navBack")
        expect(page.locator("main")).to_be_visible(timeout=10_000)

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        shot("07-phone")
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
