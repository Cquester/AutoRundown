"""UI check: the phone's reading view, rotating, Back, the diagram viewer -- free.

    python tests/test_phone_ui.py                    # sample corpus, built in a temp dir
    python tests/test_phone_ui.py --index sample.db --headed

Drives the real page against a read-only index (agent review, 2026-10-28):

  - a phone (375 px) with no saved choice opens a page in Reading view, a
    wide screen in Original layout; a saved choice is kept on both;
  - the page being read follows the phone turning: the Document view in
    portrait, the slide-over panel in landscape, and back;
  - Back with the reference pop-up open closes the pop-up and leaves the
    page behind it; the same for the full-size diagram viewer;
  - the diagram viewer (opened with made-up component codes: the sample
    corpus has no wiring diagrams) has a tap target of at least 44 px per
    code, says what a tapped code is, and zooms;
  - the car box finds "190 e 2.3 16" (a bare 16 is not the year 2016).

Anything that is not a GET, and /api/ask, is aborted and fails the run.
Never spends API credit.
"""
from _paths import ROOT
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from test_ui_picks import start_server  # noqa: E402  (same folder)

FAILURES = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def build_sample():
    tmp = Path(tempfile.mkdtemp(prefix="ar_phone_ui_"))
    env = {**os.environ, "PYTHONUTF8": "1", "AUTORUNDOWN_HOME": str(tmp / "home")}
    subprocess.run([sys.executable, "tools/make_sample_corpus.py"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    db = tmp / "index.db"
    subprocess.run([sys.executable, "ingest.py", "--archive", str(ROOT / "sample_corpus"),
                    "--index", str(db)], cwd=ROOT, env=env, check=True, capture_output=True)
    return db


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--index", help="a WIS index (default: the sample corpus, built in a temp dir)")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("needs Playwright: pip install -r requirements-dev.txt")
    db = Path(args.index) if args.index else build_sample()
    srv, base = start_server(db)
    forbidden, errors = [], []

    def on_api(route, request):
        u = urlparse(request.url)
        if request.method != "GET" or u.path == "/api/ask":
            forbidden.append(f"{request.method} {u.path}")
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

        def new_page(width, height, store=None):
            c = browser.new_context(viewport={"width": width, "height": height})
            if store:
                c.add_init_script(f"localStorage.setItem({store[0]!r}, {store[1]!r})")
            pg = c.new_page()
            pg.route("**/api/**", on_api)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base)
            pg.wait_for_function("vehicles.length > 0")
            return pg

        # a page to open: any page of the first car
        pg = new_page(375, 812)
        doc, page_no = pg.evaluate("""async () => {
          for (const v of vehicles) {
            const hits = await api(`/api/search?doc_id=${encodeURIComponent(v.doc_id)}&q=torque&limit=3`);
            if (hits.length) return [v.doc_id, String(hits[0].page_no)];
          }
          return [null, null];
        }""")
        check("the sample has a page to open", bool(doc and page_no), f"{doc} {page_no}")

        print("\n-- the reading view's default --")
        check("a phone with no saved choice: Reading view", pg.evaluate("wantOriginal()") is False)
        wide = new_page(1280, 900)
        check("a wide screen with no saved choice: Original layout", wide.evaluate("wantOriginal()") is True)
        saved_o = new_page(375, 812, ("autorundown.pageView.v3", "original"))
        check("a phone's saved choice of Original layout is kept", saved_o.evaluate("wantOriginal()") is True)
        saved_r = new_page(1280, 900, ("autorundown.pageView.v3", "reading"))
        check("a wide screen's saved choice of Reading view is kept", saved_r.evaluate("wantOriginal()") is False)

        print("\n-- turning the phone --")
        pg.evaluate("(a) => showPage(a[1], a[0])", [doc, page_no])
        pg.wait_for_function("document.body.classList.contains('docview')")
        check("portrait: the page is in the Document view", pg.evaluate("docShown()"))
        pg.set_viewport_size({"width": 800, "height": 400})        # landscape: 701-900 px
        pg.wait_for_timeout(300)
        check("landscape: the page is still shown (slide-over panel)",
              pg.evaluate("docShown()") and pg.evaluate("$('#right').classList.contains('open')"),
              pg.evaluate("[document.body.className, $('#right').className]"))
        pg.set_viewport_size({"width": 375, "height": 812})
        pg.wait_for_timeout(300)
        check("back to portrait: the Document view again",
              pg.evaluate("docShown()") and pg.evaluate("document.body.classList.contains('docview')"),
              pg.evaluate("[document.body.className, $('#right').className]"))

        print("\n-- Back with a pop-up open --")
        pg.evaluate("(a) => peekPage(a[1], a[0])", [doc, page_no])
        pg.wait_for_function("$('#peekDlg').open")
        pg.go_back()
        pg.wait_for_function("!$('#peekDlg').open")
        check("Back closes the pop-up", not pg.evaluate("$('#peekDlg').open"))
        check("... and leaves the page behind it open", pg.evaluate("docShown()"))
        pg.evaluate("(a) => peekPage(a[1], a[0])", [doc, page_no])
        pg.wait_for_function("$('#peekDlg').open")
        pg.click("#peekClose")
        pg.wait_for_timeout(300)
        check("closing it with Close: the page still open, one history entry fewer",
              pg.evaluate("docShown()") and not pg.evaluate("!!(history.state && history.state.peek)"))

        print("\n-- the full-size diagram viewer --")
        pg.evaluate("""() => {
          curComps = {doc: viewDoc(), list: [
            {code: 'X63/4', name: 'Test connector', at: [[0.30, 0.40]], pages: []},
            {code: 'N3/10', name: 'Test control unit', at: [[0.70, 0.60]], pages: []}]};
          diaOpen('/icons/icon-512.png');
        }""")
        pg.wait_for_function("$('#diaDlg').open")
        hots = pg.locator("#diaBody .hot")
        check("a tap target per code", hots.count() == 2, hots.count())
        box = hots.first.bounding_box()
        check("each at least 44 px on a phone", box and box["width"] >= 43.5 and box["height"] >= 43.5, box)
        hots.first.click()
        check("a tapped code says what it is", "X63/4" in pg.inner_text("#diaInfo")
              and "Test connector" in pg.inner_text("#diaInfo"), pg.inner_text("#diaInfo"))
        w0 = pg.evaluate("$('#diaBody .diawrap').getBoundingClientRect().width")
        pg.click("#diaIn")
        w1 = pg.evaluate("$('#diaBody .diawrap').getBoundingClientRect().width")
        check("+ zooms the picture", w1 > w0 * 1.4, (w0, w1))
        pg.click("#diaFit")
        check("Fit returns it",
              abs(pg.evaluate("$('#diaBody .diawrap').getBoundingClientRect().width") - w0) < 2)
        pg.go_back()
        pg.wait_for_function("!$('#diaDlg').open")
        check("Back closes the viewer and leaves the page", pg.evaluate("docShown()"))

        print("\n-- the car box --")
        r = pg.evaluate("""() => {
          buildCarIndex();
          const a = carMatches('190 e 2.3 16'), b = carMatches('09 e550');
          return {a: a.hits.map(h => h.label), ay: a.year, b: b.hits.length, by: b.year};
        }""")
        check('"190 e 2.3 16" finds the 2.3-16, no year', r["ay"] is None
              and any("2.3" in x and "16" in x for x in r["a"]), r)
        check('"09 e550" still reads 09 as 2009', r["by"] == 2009, r)

    srv.should_exit = True
    check("no page errors", not errors, errors[:3])
    check("nothing but GETs, and no /api/ask", not forbidden, forbidden[:3])
    print(f"\n{'all passed' if not FAILURES else str(len(FAILURES)) + ' failed: ' + '; '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
