"""UI check: part numbers copy their digits; superseded lines; the parts list -- free.

    python tests/test_parts_list.py            # sample WIS + EPC index, built in a temp dir
    python tests/test_parts_list.py --headed

Drives the real page (Playwright, the installed Edge/Chrome) against the
stand-in parts catalogue (tools/make_sample_epc.py) and checks what the user
asked for on 2026-10-29/30:

  - a tap on a part number copies its digits only ("A 210 321 23 04" ->
    "2103212304"; an N number keeps its 12 digits), on a secure page
    (navigator.clipboard) and on a plain-HTTP one (a phone on the shop
    network: the older copy command); copying adds nothing to the list;
  - a superseded line (EPC's "replaced by") says what replaced it, and the
    callout's other line says it is current;
  - "Add" on a line puts the part in the parts list (with its callout, page
    and car); a second tap takes it off; picks narrow the list first;
  - the "Parts list (n)" button (the window's right edge on a wide screen)
    opens the list over the whole page: by car, then by the catalogue's section
    ("32 Suspension / 005 Front springs"); a section opens its drawing in
    the pane, its lines show what is already in the list, and Add there
    adds more without closing the drawing;
  - a part saved without a callout (0.1.9's first list) keeps its row's
    columns, RockAuto inside the page;
  - in the list: a number copies, the quantity is kept, x takes a part off,
    "Copy all" gives digits, quantity and name per line, the RockAuto link is
    RockAuto's own part-number search in a new tab (the app never asks
    RockAuto anything), and the list survives a reload.

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
        page = "#srcout .ppage"

        def open_page(pno):
            pg.evaluate("([pno, doc]) => showPage(pno, doc)", [pno, docs[pno]])
            pg.wait_for_selector(f"{page} .pall", timeout=10_000)
            # the drawing's callouts redraw the page's picks once it has loaded:
            # act after that (a slow box)
            pg.evaluate("document.querySelectorAll('#srcout .pdwrap img').forEach(i => i.scrollIntoView())")
            pg.wait_for_function("[...document.querySelectorAll('#srcout .pdwrap')].every(w => w.dataset.done"
                                 " && w.querySelector('img').complete)", timeout=15_000)
            pg.wait_for_timeout(300)

        def row(pn, where=page):
            return pg.locator(f"{where} .pall tr", has=pg.locator(f".pncopy[data-pn='{pn}']"))

        def tap(loc):
            # a copy button says what it did once the copy has finished
            loc.click()
            pg.wait_for_function("b => b.classList.contains('flash')", arg=loc.element_handle(), timeout=5_000)
            return loc.inner_text()   # before it turns back into the number (1.6 s)

        open_page("P900004")
        check("list: no Parts list button while the list is empty",
              pg.locator(".plistbtn:visible").count() == 0)

        # -- superseded lines ---------------------------------------------------
        old, cur = row("A 210 323 13 65"), row("A 210 323 15 65")
        check("superseded: the old line says what replaced it",
              "old" in (old.get_attribute("class") or "")
              and old.locator(".ptag.old").inner_text() == "Superseded by A 210 323 15 65"
              and "fn 039: A 240011" in old.inner_text() and "replaced by" not in old.inner_text(),
              old.inner_text())
        check("superseded: the callout's other line is current",
              cur.locator(".ptag.cur").inner_text() == "Current")
        check("superseded: a callout with none has no tag", row("A 210 321 23 04").locator(".ptag").count() == 0)

        # -- copy ------------------------------------------------------------------
        said = tap(row("A 210 321 23 04").locator(".pncopy"))
        check("copy: the digits only, no letter or spaces", clip() == "2103212304", repr(clip()))
        check("copy: the button says so", "Copied 2103212304" in said, said)
        check("copy: copying adds nothing to the list", stored() == [], str(stored()))
        pg.evaluate("Object.defineProperty(window, 'isSecureContext', {value: false})")
        pg.evaluate("navigator.clipboard.writeText('-')")
        tap(row("A 210 321 24 04").locator(".pncopy"))
        check("copy: without navigator.clipboard (plain HTTP) the copy command copies",
              clip() == "2103212404", repr(clip()))

        # -- pick, then Add ------------------------------------------------------
        cur.locator("td").nth(2).click()
        pg.wait_for_timeout(200)
        shown = pg.evaluate("[...document.querySelectorAll('#srcout .pall tbody tr:not(.pfout)')].map(r => r.dataset.cpn)")
        check("pick: the list narrows to the picked number's lines",
              shown == ["A 210 323 13 65", "A 210 323 15 65"], str(shown))
        add = cur.locator(".padd")
        add.click()
        s = stored()
        check("add: the part goes into the list, with its callout, page and car",
              len(s) == 1 and s[0]["pn"] == "A 210 323 15 65" and s[0]["co"] == "40"
              and s[0]["page"] == f"{docs['P900004']}|P900004" and "FRONT SPRINGS" in s[0]["title"]
              and "W210" in s[0]["car"] and s[0]["qty"] == 1, str(s))
        check("add: the button says it is in the list", add.inner_text().startswith("In list")
              and add.get_attribute("aria-pressed") == "true", add.inner_text())
        check("add: the Parts list button shows the count, on the window's edge",
              pg.locator(".edgeplist").inner_text() == "Parts list (1)" and pg.locator(".edgeplist").is_visible())
        add.click()
        check("add: a second tap takes it off", stored() == [] and add.inner_text() == "Add", str(stored()))
        add.click()
        pg.click(f"{page} [data-clear-picks]")
        row("A 210 321 23 04").locator(".padd").click()

        # -- another drawing; an N number --------------------------------------
        open_page("P900007")
        row("N 000000 004321").locator(".padd").click()
        check("add: parts from several drawings", [x["pn"] for x in stored()]
              == ["A 210 323 15 65", "A 210 321 23 04", "N 000000 004321"], str(stored()))

        # -- the pane --------------------------------------------------------------
        pg.reload()
        pg.wait_for_function("vehicles.length > 0")
        check("list: kept after a reload", len(stored()) == 3)
        pg.click(".edgeplist")
        pg.wait_for_selector("#plistDlg[open]")
        box = pg.locator("#plistDlg").bounding_box()
        check("pane: fills the page", box["x"] <= 0.5 and box["width"] >= 1399 and box["height"] >= 899, str(box))
        heads = pg.locator("#plistDlg .plsechead b").all_inner_texts()
        check("pane: grouped by the catalogue's section",
              heads == ["32 Suspension / 005 Front springs", "01 Crankcase / 030 Oil pan"], str(heads))
        check("pane: a section says its catalogue",
              pg.locator("#plistDlg .plsechead .muted").first.inner_text() == "catalog S10")
        rows = pg.locator("#plistDlg .plrow")
        check("pane: every part listed, with its callout", rows.count() == 3
              and rows.first.locator(".cbig").inner_text() == "40")
        ra = rows.first.locator("a")
        check("pane: RockAuto is its own part-number search, in a new tab",
              ra.get_attribute("href") == "https://www.rockauto.com/en/partsearch/?partnum=2103231565"
              and ra.get_attribute("target") == "_blank", ra.get_attribute("href"))
        q = rows.first.locator("input[type=number]")
        q.fill("4")
        q.dispatch_event("change")
        check("pane: the quantity is kept", stored()[0]["qty"] == 4, str(stored()[0]))
        tap(rows.nth(2).locator(".pncopy"))
        check("pane: a number copies", clip() == "000000004321", repr(clip()))

        # a section's drawing, in the pane: check a part, add more
        sec = pg.locator("#plistDlg .plsec").first
        sec.locator("details.pldraw > summary").click()
        pg.wait_for_selector("#plistDlg .plsec .ppage .pall", timeout=10_000)
        inpane = "#plistDlg .plsec .ppage"
        states = pg.evaluate(f"[...document.querySelectorAll('{inpane} .pall tr')].map(r => "
                             "[r.dataset.cpn, r.querySelector('.padd').textContent])")
        check("pane: the drawing's lines show what is already in the list",
              dict(states)["A 210 323 15 65"].startswith("In list") and dict(states)["A 210 321 24 04"] == "Add",
              str(states))
        row("A 210 321 24 04", inpane).locator(".padd").click()
        pg.wait_for_timeout(200)
        check("pane: Add in the drawing adds to the list, under its section",
              len(stored()) == 4 and stored()[3]["pn"] == "A 210 321 24 04"
              and pg.locator("#plistDlg .plsec").first.locator(".plrow").count() == 3, str(stored()))
        check("pane: the drawing stays open after adding",
              pg.locator("#plistDlg details.pldraw[open] .ppage").count() == 1)

        tap(pg.locator("#plistCopyAll"))
        check("pane: Copy all gives digits, quantity and name per line",
              clip().splitlines() == ["2103231565\t4\tSTABILIZER", "2103212304\t1\tCOIL SPRING",
                                      "000000004321\t1\tSELF-TAPPING SCREW",
                                      "2103212404\t1\tCOIL SPRING, REINFORCED"], repr(clip()))
        pg.locator("#plistDlg .plrow").nth(1).locator("[data-plist-del]").click()
        check("pane: x takes a part off", [x["pn"] for x in stored()]
              == ["A 210 323 15 65", "N 000000 004321", "A 210 321 24 04"], str(stored()))
        pg.click("#plistClose")
        check("pane: the count follows", pg.locator(".edgeplist").inner_text() == "Parts list (3)")

        # a phone: no edge button; the pane fills the screen
        pg.set_viewport_size({"width": 375, "height": 812})
        pg.wait_for_timeout(200)
        check("phone: no button on the screen's edge", not pg.locator(".edgeplist").is_visible())
        pg.set_viewport_size({"width": 1000, "height": 800})
        pg.evaluate("""() => { const l = plistLoad(); l.push({pn: 'N 304017 005002', qty: 1, car: 'X',
                 name: 'HEXAGON HEAD BOLT, BRACKET TO AUTOMATIC ANTENNA', fits: 'qty 2, all models'});
                 localStorage.setItem(PLIST_KEY, JSON.stringify(l)); renderPlist(); plistShow(); }""")
        fit = pg.evaluate("""() => { const b = document.querySelector('#plistBody'),
                 r = [...b.querySelectorAll('.plrow')].pop(), ra = r.querySelector('a').getBoundingClientRect();
                 return {over: b.scrollWidth - b.clientWidth, raRight: ra.right, bodyRight: b.getBoundingClientRect().right}; }""")
        check("pane: a part saved without a callout keeps its row inside the page (RockAuto shown)",
              fit["over"] <= 0 and fit["raRight"] <= fit["bodyRight"], str(fit))
        pg.evaluate("$('#plistDlg').close()")
        pg.set_viewport_size({"width": 375, "height": 812})
        pg.evaluate("renderPlist(); plistShow()")
        box = pg.locator("#plistDlg").bounding_box()
        check("phone: the pane fills the screen", box["width"] >= 374 and box["height"] >= 811, str(box))

        check("no non-GET request", not forbidden, str(forbidden))
        check("no request leaves this machine", not outside, str(outside))
        check("no page errors", not errors, str(errors))
        browser.close()
    srv.should_exit = True
    print("all passed" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
