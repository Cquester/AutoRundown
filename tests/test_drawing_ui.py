"""UI check: the Manual contents, parts drawings and the divider -- free.

    python tests/test_drawing_ui.py            # sample WIS + EPC index, built in a temp dir
    python tests/test_drawing_ui.py --headed

Drives the real page (Playwright) against a read-only sample index with the
stand-in parts catalogue (tools/make_sample_epc.py: a W210 brake page with
two drawings and callout sidecars) and checks what the user asked for on
2026-10-23/24:

  - the Manual page tab starts as the car's contents; a section opens, a
    document opens as the page with "Manual contents", which comes back with
    the section still open; Clear question shows the contents again;
  - the page opens in Original layout after ordinary clicks, and Reading
    view is remembered only when picked;
  - a parts page with several drawings lists under each drawing the parts
    printed on it, and the rest under "Other parts on this page";
  - a tapped number turns red (on the drawing and in the list); several can
    be picked; a second tap takes one off; a row in a folded drawing's list
    opens that drawing; Clear empties the picks;
  - the divider between the sides drags, stops at its limits, is kept after
    a reload, resets on double-click, and is hidden on a phone.

Anything that is not a GET fails the run (nothing here may write or ask).
"""
from _paths import ROOT
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from test_ui_picks import start_server  # noqa: E402  (same folder)

FAILURES = []
RED = "rgb(212, 0, 0)"


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def build_sample() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ar_drawing_ui_"))
    env = {**os.environ, "PYTHONUTF8": "1", "AUTORUNDOWN_HOME": str(tmp / "home")}

    def run(*cmd):
        subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=True, capture_output=True)
    db = tmp / "index.db"
    run("tools/make_sample_corpus.py")
    run("ingest.py", "--archive", str(ROOT / "sample_corpus"), "--index", str(db))
    run("tools/make_sample_epc.py", "--out", str(tmp / "epc"))
    run("ingest.py", "--archive", str(tmp / "epc"), "--index", str(db))
    return db


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("needs Playwright: pip install -r requirements-dev.txt")
    db = build_sample()
    con = sqlite3.connect(db)
    epc_doc = con.execute("SELECT doc_id FROM pages WHERE page_no = 'P900003'").fetchone()[0]
    con.close()
    srv, base = start_server(db)
    forbidden, errors = [], []

    def on_api(route, request):
        if request.method != "GET":
            forbidden.append(f"{request.method} {urlparse(request.url).path}")
            return route.abort()
        route.continue_()

    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(channel=channel, headless=not args.headed)
                break
            except Exception:
                continue
        if browser is None:
            raise SystemExit("no browser: install Edge/Chrome, or `playwright install chromium`")
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.route("**/api/**", on_api)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base)
        pg.wait_for_function("vehicles.length > 0")
        pg.fill("#carInput", "W210")
        pg.wait_for_timeout(400)
        pg.locator("#carList .opt").first.click()

        # -- Manual contents --------------------------------------------------
        pg.wait_for_selector("#srcout .mbrowse .mbsec", timeout=15_000)
        check("contents: the Manual page tab starts as the car's contents",
              "by section" in pg.inner_text("#srcout .mbrowse"))
        first = pg.locator("#srcout .mbrowse > .mbsec").first
        first.locator("summary").click()
        pg.wait_for_selector("#srcout .mbrowse .mbpage, #srcout .mbrowse .mbsec .mbsec", timeout=10_000)
        while pg.locator("#srcout .mbrowse .mbpage").count() == 0:      # down to a page
            sub = pg.locator("#srcout .mbrowse .mbsec:not([open])").first
            sub.locator("summary").click()
            pg.wait_for_timeout(400)
        title = pg.locator("#srcout .mbpage").first.inner_text().split("\n")[-1]
        pg.locator("#srcout .mbpage").first.click()
        pg.wait_for_selector("#srcout [data-manual-contents]", timeout=10_000)
        check("contents: a document opens as its page", title.strip() in pg.inner_text("#srcout h3"),
              f"{title!r} vs {pg.inner_text('#srcout h3')!r}")
        # every click above went through the page's click handler, which once
        # matched <body data-view> and saved "reading" (2026-10-25)
        view = lambda: pg.evaluate("localStorage.getItem(ORIGINAL_KEY)")
        # (the sample WIS has no page layouts, so the page then falls back to
        # its text by itself -- "No original layout ..." -- without saving)
        pg.wait_for_selector("#srcout #origFrame, #srcout #origNone", timeout=10_000)
        check("view: ordinary clicks leave the page on Original layout",
              view() is None and pg.evaluate("wantOriginal()"), f"stored {view()!r}")
        pg.click("#srcout .viewtoggle [data-view=reading]")
        check("view: Reading view is kept only when picked",
              view() == "reading" and pg.locator("#srcout #readingView").is_visible(), repr(view()))
        pg.evaluate("localStorage.removeItem(ORIGINAL_KEY)")
        pg.click("#srcout [data-manual-contents]")
        pg.wait_for_selector("#srcout .mbrowse", timeout=5_000)
        check("contents: 'Manual contents' comes back with the section still open",
              pg.locator("#srcout .mbrowse > .mbsec[open]").count() >= 1)

        # -- a parts page with two drawings -----------------------------------
        pg.evaluate("([pno, doc]) => showPage(pno, doc)", ["P900003", epc_doc])
        pg.wait_for_selector("#srcout .ppage .pdsec", timeout=10_000)
        # drawings load lazily: open and scroll each into view
        for i in range(pg.locator("#srcout .pdsec").count()):
            pg.evaluate("i => { const d = document.querySelectorAll('#srcout .pdsec')[i]; d.open = true;"
                        " d.querySelector('img').scrollIntoView(); }", i)
            pg.wait_for_timeout(300)
        pg.wait_for_function("document.querySelectorAll('#srcout .pco').length >= 3", timeout=10_000)
        heads = pg.locator("#srcout .pdsec > summary").all_inner_texts()
        check("drawings: each dropdown says how many parts it shows",
              [h.split("—")[-1].strip() for h in heads] == ["2 parts", "1 part"], str(heads))
        per = pg.evaluate("[...document.querySelectorAll('#srcout .pdsec')].map(s =>"
                          " [...s.querySelectorAll('.pdparts td.co')].map(t => t.textContent))")
        check("drawings: each lists the parts printed on it", per == [["10", "30"], ["20"]], str(per))
        other = pg.evaluate("[...document.querySelectorAll('#srcout .pall tbody tr:not([hidden])')]"
                            ".map(r => r.textContent)")
        check("drawings: a part on no drawing stays under 'Other parts on this page'",
              len(other) == 1 and "CALIPER, RIGHT" in other[0]
              and pg.inner_text("#srcout .pall .sechead").lower() == "other parts on this page", str(other))
        pg.evaluate("document.querySelectorAll('#srcout .pdsec')[1].open = false")

        def state():
            return pg.evaluate("""() => { const h = document.querySelector('#srcout .ppage');
                return {marks: [...h.querySelectorAll('.pco.on')].map(b => b.dataset.co),
                        rows: [...new Set([...h.querySelectorAll('tr.on')].map(r => r.dataset.callout))],
                        listed: [...h.querySelectorAll('.pfline .cbig')].map(b => b.textContent),
                        hidden: h.querySelector('.pfind').hidden}; }""")

        def tap(co):
            pg.evaluate("co => [...document.querySelectorAll('#srcout .pco')].find(b => b.dataset.co === co).click()", co)
            pg.wait_for_timeout(150)

        tap("10")
        mark = pg.evaluate("getComputedStyle([...document.querySelectorAll('#srcout .pco.on')][0]).color")
        row = pg.evaluate("getComputedStyle(document.querySelector('#srcout tr.on td.co')).color")
        check("pick: a tapped number turns red on the drawing and in the list",
              mark == RED and row == RED, f"{mark} / {row}")
        check("pick: its part is named above the drawing", state()["listed"] == ["10"], str(state()))
        tap("30")
        s = state()
        check("pick: several at once", s["marks"] == ["10", "30"] and s["listed"] == ["10", "30"], str(s))
        tap("10")
        s = state()
        check("pick: a second tap takes one off", s["marks"] == ["30"] and s["rows"] == ["30"], str(s))
        pg.evaluate("[...document.querySelectorAll('#srcout .pdsec')[1].querySelectorAll('tr[data-callout]')]"
                    ".find(r => r.dataset.callout === '20').click()")
        pg.wait_for_timeout(300)
        s = state()
        check("pick: a row in a folded drawing's list opens it and marks its number",
              pg.evaluate("document.querySelectorAll('#srcout .pdsec')[1].open")
              and set(s["marks"]) == {"30", "20"}, str(s))
        pg.click("#srcout [data-clear-picks]")
        s = state()
        check("pick: Clear empties the picks", not s["marks"] and not s["rows"] and s["hidden"], str(s))

        # -- the divider ------------------------------------------------------
        def left():
            return round(pg.evaluate("document.querySelector('#left').getBoundingClientRect().width"))
        start = left()
        box = pg.locator("#splitter").bounding_box()
        y = box["y"] + 200
        pg.mouse.move(box["x"] + box["width"] / 2, y)
        pg.mouse.down()
        pg.mouse.move(600, y, steps=8)
        pg.mouse.up()
        check("divider: a drag resizes the sides", abs(left() - 600) <= 6, f"{start} -> {left()}")
        box = pg.locator("#splitter").bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, y)
        pg.mouse.down()
        pg.mouse.move(40, y, steps=8)
        pg.mouse.up()
        check("divider: the question side keeps at least 300 px", abs(left() - 300) <= 2, str(left()))
        pg.focus("#splitter")
        pg.keyboard.press("ArrowRight")
        check("divider: arrow keys move it", left() > 300, str(left()))
        kept = left()
        pg.reload()
        pg.wait_for_function("vehicles.length > 0")
        pg.wait_for_timeout(300)
        check("divider: kept after a reload", abs(left() - kept) <= 2, f"{kept} -> {left()}")
        pg.dblclick("#splitter")
        pg.wait_for_timeout(100)
        check("divider: double-click puts the layout back", abs(left() - start) <= 2, f"{start} vs {left()}")
        pg.set_viewport_size({"width": 375, "height": 812})
        pg.wait_for_timeout(200)
        check("divider: hidden on a phone", not pg.locator("#splitter").is_visible())

        browser.close()
    srv.should_exit = True
    check("no request other than GET", not forbidden, str(forbidden))
    check("no page errors", not errors, "; ".join(errors))
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
