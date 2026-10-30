"""UI check: part numbers copy their digits; the parts list -- free.

    python tests/test_parts_list.py            # sample WIS + EPC index, built in a temp dir
    python tests/test_parts_list.py --headed

Drives the real page (Playwright, the installed Edge/Chrome) against the
stand-in parts catalogue (tools/make_sample_epc.py) and checks what the user
asked for on 2026-10-29:

  - a tap on a picked part's number copies its digits only
    ("A 210 321 23 04" -> "2103212304"; an N number keeps its 12 digits),
    on a secure page (navigator.clipboard) and on a plain-HTTP one (a phone
    on the shop network: the older copy command);
  - every number under a callout is offered, each its own button;
  - the tapped part goes into the parts list, once, across drawings; the
    "Parts list (n)" button shows the count and opens the list, which
    survives a reload (this browser's own storage);
  - in the list: a number copies again, the quantity is kept, x takes a
    part off, "Copy all" gives digits, quantity and name per line, and the
    RockAuto link is RockAuto's own part-number search, opened in a new tab
    (the app itself never asks RockAuto anything).

Anything that is not a GET fails the run (nothing here may write or ask),
and so does any request that leaves this machine.
"""
from _paths import ROOT  # noqa: F401
import argparse
import sqlite3
import sys
from urllib.parse import urlparse

from test_drawing_ui import build_sample  # noqa: E402  (same folder)
from test_ui_picks import start_server  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


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
    docs = dict(con.execute("SELECT page_no, doc_id FROM pages WHERE page_no IN ('P900004', 'P900007')"))
    con.close()
    srv, base = start_server(db)
    forbidden, errors, outside = [], [], []

    def on_req(route, request):
        host = urlparse(request.url).hostname
        if host not in ("127.0.0.1", "localhost"):
            outside.append(request.url)
            return route.abort()
        if "/api/" in request.url and request.method != "GET":
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
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
        pg = ctx.new_page()
        pg.route("**/*", on_req)
        pg.on("pageerror", lambda e: errors.append(f"{e} {e.stack}"))
        pg.goto(base)
        pg.wait_for_function("vehicles.length > 0")
        pg.evaluate("localStorage.removeItem(PLIST_KEY)")
        pg.fill("#carInput", "W210")
        pg.wait_for_timeout(400)
        pg.locator("#carList .opt").first.click()
        pg.wait_for_timeout(500)

        clip = lambda: pg.evaluate("navigator.clipboard.readText()")
        stored = lambda: pg.evaluate("plistLoad()")

        def open_page(pno):
            pg.evaluate("([pno, doc]) => showPage(pno, doc)", [pno, docs[pno]])
            pg.wait_for_selector("#srcout .ppage .pall", timeout=10_000)
            # the drawing's callouts redraw the picks panel once it has loaded:
            # tap after that, or the button tapped is replaced (a slow box)
            pg.evaluate("document.querySelectorAll('#srcout .pdwrap img').forEach(i => i.scrollIntoView())")
            pg.wait_for_function("[...document.querySelectorAll('#srcout .pdwrap')].every(w => w.dataset.done"
                                 " && w.querySelector('img').complete)", timeout=15_000)
            pg.wait_for_timeout(300)

        def tap(loc):
            # a copy button says what it did once the copy has finished
            loc.click()
            loc.element_handle().wait_for_element_state("stable")
            pg.wait_for_function("b => b.classList.contains('flash')", arg=loc.element_handle(), timeout=5_000)
            return loc.inner_text()   # before it turns back into the number (1.6 s)

        def pick(co):
            pg.evaluate("co => [...document.querySelectorAll('#srcout .pall tr[data-callout]')]"
                        ".find(r => r.dataset.callout === co).click()", co)
            pg.wait_for_timeout(150)

        open_page("P900004")
        check("parts list: no button while the list is empty",
              pg.locator("#srcout .pfind .plistbtn").is_hidden())
        pick("5")
        btns = pg.locator("#srcout .pfind .pncopy")
        check("picks: every number under the callout is offered",
              btns.all_inner_texts() == ["A 210 321 23 04", "A 210 321 24 04"], str(btns.all_inner_texts()))

        # -- copy on a secure page (this PC) ------------------------------------
        said = tap(btns.first)
        check("copy: the digits only, no letter or spaces", clip() == "2103212304", repr(clip()))
        check("copy: the button says so", "Copied 2103212304" in said, said)
        s = stored()
        check("list: the part is added with its car",
              len(s) == 1 and s[0]["pn"] == "A 210 321 23 04" and s[0]["name"] == "COIL SPRING"
              and "W210" in s[0]["car"] and s[0]["qty"] == 1, str(s))
        check("list: the button shows the count",
              pg.inner_text("#srcout .pfind .plistbtn") == "Parts list (1)")
        pg.wait_for_function("() => !document.querySelector('#srcout .pfind .pncopy.flash')", timeout=5_000)
        said = tap(btns.first)
        check("list: a second tap adds it once", len(stored()) == 1 and "already in list" in said, said)

        # -- copy on a plain-HTTP page (a phone on the shop network) ---------
        pg.evaluate("Object.defineProperty(window, 'isSecureContext', {value: false})")
        pg.evaluate("navigator.clipboard.writeText('-')")
        tap(btns.nth(1))
        check("copy: without navigator.clipboard (plain HTTP) the copy command copies",
              clip() == "2103212404", repr(clip()))

        # -- another drawing; an N number ------------------------------------
        open_page("P900007")
        pick("30")
        tap(pg.locator("#srcout .pfind .pncopy").first)
        check("copy: an N number keeps all its digits", clip() == "000000004321", repr(clip()))
        check("list: parts from several drawings", [x["pn"] for x in stored()]
              == ["A 210 321 23 04", "A 210 321 24 04", "N 000000 004321"], str(stored()))

        # -- the list ---------------------------------------------------------
        pg.reload()
        pg.wait_for_function("vehicles.length > 0")
        check("list: kept after a reload", len(stored()) == 3)
        open_page("P900004")
        pg.click("#srcout .pfind .plistbtn")
        pg.wait_for_selector("#plistDlg[open]")
        rows = pg.locator("#plistDlg .plrow")
        check("list: opens from the button, every part listed", rows.count() == 3)
        ra = rows.first.locator("a").get_attribute("href")
        check("list: RockAuto is its own part-number search, in a new tab",
              ra == "https://www.rockauto.com/en/partsearch/?partnum=2103212304"
              and rows.first.locator("a").get_attribute("target") == "_blank", ra)
        q = rows.first.locator("input[type=number]")
        q.fill("4")
        q.dispatch_event("change")
        check("list: the quantity is kept", stored()[0]["qty"] == 4, str(stored()[0]))
        tap(rows.nth(2).locator(".pncopy"))
        check("list: a number copies again", clip() == "000000004321", repr(clip()))
        tap(pg.locator("#plistCopyAll"))
        check("list: Copy all gives digits, quantity and name per line",
              clip().splitlines() == ["2103212304\t4\tCOIL SPRING", "2103212404\t1\tCOIL SPRING, REINFORCED",
                                      "000000004321\t1\tSELF-TAPPING SCREW"], repr(clip()))
        rows.nth(1).locator("[data-plist-del]").click()
        check("list: x takes a part off", [x["pn"] for x in stored()] == ["A 210 321 23 04", "N 000000 004321"]
              and pg.locator("#plistDlg .plrow").count() == 2)
        pg.click("#plistClose")
        check("list: the count follows", pg.inner_text("#srcout .pfind .plistbtn") == "Parts list (2)")

        check("no non-GET request", not forbidden, str(forbidden))
        check("no request leaves this machine", not outside, str(outside))
        check("no page errors", not errors, str(errors))
        browser.close()
    srv.should_exit = True
    print("all passed" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
