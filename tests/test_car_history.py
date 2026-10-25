"""UI check: recent cars on this device -- free.

    python tests/test_car_history.py                    # sample WIS index, built in a temp dir
    python tests/test_car_history.py --index sample_wis.db --headed

The car box lists the last cars picked on this device (localStorage,
`autorundown.carHistory.v1`) when it is focused empty or unchanged, and the
car sheet does on a phone. This drives the real page against a read-only
index and checks:

  - three picks (typed, a VIN, Browse) -> reopen -> three rows, newest first;
  - re-selecting a row (click, and arrow + Enter) sends the same `doc_id`,
    `variant=`, `engine=` and `year=` as the original pick -- the request
    captured as in test_ui_picks.py;
  - picking a listed car again moves it to the top, no duplicate;
  - 11 picks keep 10; the x on a row removes it; "Clear history" empties it;
    Tab reaches the list's buttons;
  - a stored pick that no longer fits the index (variant name moved, manual
    gone) falls back to the series and says so, or is dropped with a note;
  - typing replaces the list with the normal suggestions;
  - a phone (375x812): the car sheet shows the list without the keyboard,
    rows at least 44 px high, one tap re-selects;
  - storage blocked (localStorage throws): no history, no page error, the
    box still picks and searches.

/api/search is answered by the test, not the server; anything that is not a
GET, and /api/ask, is aborted and fails the run. Never spends API credit.
"""
from _paths import ROOT
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from test_ui_picks import Truth, start_server  # noqa: E402  (same folder)

FAILURES = []
KEYS = ("doc_id", "variant", "engine", "year")


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def build_sample():
    tmp = Path(tempfile.mkdtemp(prefix="ar_car_history_"))
    env = {**os.environ, "PYTHONUTF8": "1", "AUTORUNDOWN_HOME": str(tmp / "home")}
    subprocess.run([sys.executable, "tools/make_sample_corpus.py"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    db = tmp / "index.db"
    subprocess.run([sys.executable, "ingest.py", "--archive", str(ROOT / "sample_corpus"),
                    "--index", str(db)], cwd=ROOT, env=env, check=True, capture_output=True)
    return db


# three cars the box can find by their own label, each of another generation
JS_CANDIDATES = """() => {
  if (!carIndexReady) buildCarIndex();
  const out = [], docs = new Set();
  for (const e of carIndex) {
    if (e.variant === null || docs.has(e.doc)) continue;
    const hits = carMatches(e.label).hits;
    if (!hits.some(h => h.label === e.label)) continue;
    docs.add(e.doc); out.push(e.label);
  }
  return out;
}"""

HIST_LABELS = "els => els.map(e => e.querySelector('.histtext > div').textContent)"


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
    truth = Truth(db)
    srv, base = start_server(db)
    sent, forbidden, errors = {}, [], []

    def on_api(route, request):
        u = urlparse(request.url)
        if request.method != "GET" or u.path == "/api/ask":
            forbidden.append(f"{request.method} {u.path}")
            return route.abort()
        if u.path == "/api/search":
            p = {k: v[0] for k, v in parse_qs(u.query).items()}
            if p.get("tab", "workshop") == "workshop":   # not the right box's tabs
                sent[p.get("q")] = p
            return route.fulfill(json=[])
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

        def new_page(blocked=False, **ctx):
            c = browser.new_context(**ctx)
            if blocked:
                c.add_init_script("""Object.defineProperty(window, 'localStorage', {
                  configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); }});""")
            pg = c.new_page()
            pg.route("**/api/**", on_api)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base)
            pg.wait_for_function("vehicles.length > 0")
            pg.evaluate("buildCarIndex()")
            return pg

        def search(pg, tag):
            if pg.locator("#searchBtn").is_hidden():
                pg.click("#modeTabs [data-mode=kw]")     # the left side's Keyword search tab
            pg.fill("#q", tag)
            with pg.expect_response(lambda r: "/api/search?" in r.url and f"q={tag}&" in r.url):
                pg.click("#searchBtn")
            p = sent.get(tag)
            if not p:
                return None
            # the year travels in the Ask body (not /api/search): what Ask would send
            return {**{k: p.get(k) for k in KEYS}, "ask_year": pg.evaluate("yearSel()")}

        def reopen(pg):
            """Leave the box, come back: the recent cars, as labels."""
            pg.click("#q")
            pg.wait_for_timeout(200)
            pg.click("#carInput")
            pg.wait_for_timeout(50)
            if pg.is_hidden("#carList"):
                return []
            return pg.eval_on_selector_all("#carList .opt.hist", HIST_LABELS)

        def type_pick(pg, label):
            pg.wait_for_timeout(200)
            pg.fill("#carInput", "")
            pg.type("#carInput", label)
            pg.wait_for_selector("#carList:not([hidden]) .opt[data-k]")
            got = pg.eval_on_selector_all("#carList .opt[data-k] > div:first-child",
                                          "els => els.map(e => e.textContent)")
            pg.click(f'#carList .opt[data-k="{got.index(label)}"]')

        def hist_row(pg, label):
            got = pg.eval_on_selector_all("#carList .opt.hist", HIST_LABELS)
            return f'#carList .opt.hist[data-k="{got.index(label)}"]'

        page = new_page(viewport={"width": 1280, "height": 800})
        labels = page.evaluate(JS_CANDIDATES)
        check("sample index has three findable cars", len(labels) >= 3, f"{labels}")
        if len(labels) < 3:
            raise SystemExit(1)
        check("no history on a new device", reopen(page) == [])

        # 1. three picks: typed, a VIN, Browse
        print("\n1. three picks, reopened")
        orig = {}
        type_pick(page, labels[0])
        orig[page.input_value("#carInput")] = search(page, "pick-typed")
        vin = next(iter(truth.vin_cases()), None)
        check("sample index has a VIN prefix", vin is not None)
        page.fill("#carInput", "")
        page.type("#carInput", vin[0])
        page.wait_for_selector("#carList:not([hidden]) .opt[data-k]")
        page.keyboard.press("Enter")
        vin_label = page.input_value("#carInput")
        orig[vin_label] = search(page, "pick-vin")
        b_doc = page.evaluate(f"carIndex.find(e => e.label === {labels[1]!r}).doc")
        b_n = page.evaluate(f"carIndex.find(e => e.label === {labels[1]!r}).variant")
        page.click("#browseBtn")
        page.select_option("#year", "")
        page.select_option("#make", "")
        page.select_option("#vehicle", b_doc)
        page.select_option("#variant", str(b_n))
        b_label = page.input_value("#carInput")
        orig[b_label] = search(page, "pick-browse")
        check("Browse pick sent its variant", orig[b_label] and orig[b_label]["variant"] == str(b_n),
              f"{orig[b_label]}")
        got = reopen(page)
        check("reopen: 3 entries, newest first", got == [b_label, vin_label, labels[0]],
              f"{got} vs {[b_label, vin_label, labels[0]]}")
        stored = page.evaluate("carHistory()")
        check("the VIN is stored with its pick", any(h.get("vin") == vin[0] for h in stored),
              f"{[h.get('vin') for h in stored]}")
        check("Browse's two steps (Model, Variant) are one entry",
              sum(h["doc"] == b_doc and h.get("src") == "browse" for h in stored) == 1)
        check("entries are structured", all({"doc", "variant", "vname", "engine", "year", "label"}
                                            <= set(h) for h in stored))

        # 2. re-select sends what the first pick sent; it moves to the top
        print("\n2. re-select")
        page.click(hist_row(page, labels[0]))                 # the oldest
        again = search(page, "again-typed")
        check("click re-select sends the same car", again == orig[labels[0]],
              f"{again} vs {orig[labels[0]]}")
        check("box shows the picked label", page.input_value("#carInput") == labels[0])
        got = reopen(page)
        check("re-selected car moves to the top, no duplicate",
              got == [labels[0], b_label, vin_label], f"{got}")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")                       # the VIN pick, third
        page.keyboard.press("Enter")
        again = search(page, "again-vin")
        check("arrow + Enter re-select of the VIN pick sends the same car",
              again == orig[vin_label], f"{again} vs {orig[vin_label]}")
        got = reopen(page)
        page.keyboard.press("ArrowUp")                        # from nothing: the last row
        page.keyboard.press("Enter")
        again = search(page, "again-browse")
        check("ArrowUp + Enter re-selects the Browse pick", again == orig[b_label],
              f"{again} vs {orig[b_label]}")
        reopen(page)
        page.keyboard.press("Enter")                          # nothing highlighted: keeps the car
        check("Enter with nothing highlighted keeps the car and closes the list",
              page.is_hidden("#carList") and page.input_value("#carInput") == b_label)

        # typing replaces the list with the normal suggestions
        reopen(page)
        page.keyboard.type(labels[2][:8])
        check("typing replaces the recent cars with suggestions",
              page.locator("#carList .opt.hist").count() == 0
              and page.locator("#carList .opt[data-k]").count() > 0)
        page.keyboard.press("Escape")

        # 3. 11 picks -> 10, the x, Tab, Clear history
        print("\n3. cap, remove, clear")
        n = page.evaluate("""() => {
          const picks = carIndex.filter(e => e.variant !== null).slice(0, 11);
          for (const e of picks) applyCar(e);
          return picks.length; }""")
        got = reopen(page)
        want = page.evaluate("carIndex.filter(e => e.variant !== null).slice(0, 11).reverse().slice(0, 10).map(e => e.label)")
        check(f"{n} picks keep 10, newest first", n == 11 and got == want, f"{len(got)}: {got[:3]}...")
        page.keyboard.press("Tab")
        check("Tab reaches Clear history",
              page.evaluate("document.activeElement.className") == "histclear")
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")                          # the first row's x
        got2 = page.eval_on_selector_all("#carList .opt.hist", HIST_LABELS)
        check("x removes that entry (keyboard)", got2 == got[1:], f"{got2[:3]}")
        check("focus back in the box after x", page.evaluate("document.activeElement.id") == "carInput")
        page.click("#carList .opt.hist:nth-child(2) .histx")
        got3 = page.eval_on_selector_all("#carList .opt.hist", HIST_LABELS)
        check("x removes that entry (mouse), list stays open", got3 == got[2:], f"{got3[:3]}")
        page.click("#carList .histclear")
        check("Clear history closes the list", page.is_hidden("#carList"))
        check("Clear history empties it", reopen(page) == [] and page.evaluate("carHistory().length") == 0)

        # 4. a stored pick that no longer fits the index
        print("\n4. stale picks")
        e = page.evaluate(f"carIndex.find(e => e.label === {labels[2]!r})")
        page.evaluate("""e => localStorage.setItem('autorundown.carHistory.v1', JSON.stringify([
          {doc: 'no-such-manual', variant: 0, vname: 'X', engine: null, year: null, label: 'Gone car', sub: ''},
          {doc: e.doc, variant: e.variant, vname: 'Renamed since', engine: e.engine, year: e.year,
           label: e.label, sub: e.sub}]))""", e)
        reopen(page)
        page.click("#carList .opt.hist:nth-child(2)")         # the gone one
        check("a car no longer indexed is dropped, with a note",
              "no longer" in (page.text_content("#carList .histnote") or "")
              and page.evaluate("carHistory().length") == 1)
        page.click("#carList .opt.hist:nth-child(3)")         # note, head, entry
        v = truth.vehicles[e["doc"]]
        check("a moved variant falls back to the whole series, and says so",
              "not listed the same way" in (page.text_content("#left") or ""))
        check("... the box names the series", v["model"] in page.input_value("#carInput"),
              page.input_value("#carInput"))
        p = search(page, "stale")
        check("... and searches the series: no variant, no engine",
              p and p["doc_id"] == e["doc"] and p["variant"] is None and p["engine"] is None, f"{p}")
        page.close()

        # 5. a phone: the car sheet shows the list, rows a thumb can hit
        print("\n5. phone 375x812")
        ph = new_page(viewport={"width": 375, "height": 812}, has_touch=True, is_mobile=True)
        ph.evaluate(f"""() => {{ for (const l of {labels[:3]!r}) applyCar(carIndex.find(e => e.label === l)); }}""")
        before = search(ph, "phone-before")
        ph.evaluate(f"applyCar(carIndex.find(e => e.label === {labels[0]!r}))")
        ph.click("#carPill")
        ph.wait_for_timeout(100)
        got = ph.eval_on_selector_all("#carList .opt.hist", HIST_LABELS) if ph.is_visible("#carList") else []
        check("car sheet shows the recent cars", got[:3] == [labels[0], labels[2], labels[1]], f"{got}")
        check("... without the keyboard (box not focused)",
              ph.evaluate("document.activeElement.id") != "carInput")
        hs = ph.eval_on_selector_all("#carList .opt.hist, #carList .histx, #carList .histclear",
                                     "els => els.map(e => e.getBoundingClientRect().height)")
        check("rows and buttons at least 44 px", hs and min(hs) >= 44, f"{hs}")
        wide = ph.evaluate("document.documentElement.scrollWidth <= innerWidth")
        check("no sideways scroll", wide)
        ph.tap(hist_row(ph, labels[1]))
        check("one tap closes the sheet", ph.evaluate("!document.body.classList.contains('sheet')"))
        check("... and picks that car", ph.input_value("#carInput") == labels[1])
        got = search(ph, "phone-tap")
        ph.evaluate(f"applyCar(carIndex.find(e => e.label === {labels[1]!r}))")
        want = search(ph, "phone-direct")
        check("tap re-select sends the same car as picking it", got == want, f"{got} vs {want}")
        check("phone search worked", before is not None)
        ph.close()

        # 6. storage blocked
        print("\n6. storage blocked")
        n_err = len(errors)
        bl = new_page(blocked=True, viewport={"width": 1280, "height": 800})
        type_pick(bl, labels[0])
        p = search(bl, "blocked")
        check("blocked storage: the box still picks and searches", p is not None and p["doc_id"])
        check("blocked storage: no history shown", reopen(bl) == [] and bl.is_hidden("#carList"))
        check("blocked storage: carHistory() is empty", bl.evaluate("carHistory().length") == 0)
        check("blocked storage: no page errors", len(errors) == n_err, f"{errors[n_err:]}")
        bl.close()
        browser.close()
    srv.should_exit = True

    check("no page errors", not errors, f"{errors[:3]}")
    check("nothing but GETs, no /api/ask", not forbidden, f"{forbidden}")
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
