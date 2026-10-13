"""Screenshots for the README, taken from a running instance with sample data.

    python tools/readme_shots.py --url http://localhost:8757 --out docs/images

Free: keyword search only, never /api/ask (any request to it is aborted).
Run it against `dev.py` or a test box (`tools/container/box.sh up`, then
`box.sh exec <name> python tools/readme_shots.py --url http://localhost:8757
--out /tmp/shots` and `box.sh fetch <name> /tmp/shots`). The sample manuals
are invented text, so the pictures show no real manual content.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

# (file, viewport, car typed in the box, keyword search, open which hit)
SHOTS = [
    ("search-desktop.png", (1280, 760), "2005 E500 4matic", "tightening torques front axle", 1),
    ("search-phone.png", (390, 844), "2005 E500 4matic", "tightening torques front axle", None),
]


def shoot(page, url, out, name, size, car, query, hit):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(url)
    expect(page.locator("main")).to_be_visible(timeout=30_000)
    page.fill("#carInput", car)
    expect(page.locator("#carList .opt[data-k]").first).to_be_visible(timeout=10_000)
    page.press("#carInput", "Enter")
    page.fill("#q", query)
    page.click("#searchBtn")
    hits = page.locator("#out .hit")
    expect(hits.first).to_be_visible(timeout=10_000)
    if hit is not None:
        hits.nth(hit).click()
        expect(page.locator("#srcout")).not_to_contain_text(
            "Cited pages and diagrams appear here", timeout=10_000)
    page.mouse.move(0, 0)
    page.wait_for_timeout(400)
    page.screenshot(path=str(out / name))
    print("wrote", out / name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8757")
    ap.add_argument("--out", default="docs/images")
    ap.add_argument("--executable", help="a Chromium/Edge binary (default: Playwright's)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.executable)
        page = browser.new_page(color_scheme="dark", device_scale_factor=1)
        page.route("**/api/ask*", lambda r: r.abort())   # never spend
        for name, size, car, query, hit in SHOTS:
            shoot(page, args.url, out, name, size, car, query, hit)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
