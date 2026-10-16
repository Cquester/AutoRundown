"""UI check: one question fills the right box's tabs -- free.

    python tests/test_answer_tabs.py                    # sample WIS index, built in a temp dir
    python tests/test_answer_tabs.py --index sample.db --headed

The right box has tabs -- Manual page, Parts, Labour times, Capacities,
Torque specs -- and every Ask or Search fills them all (the user,
2026-10-16). After an Ask they are searched with the AI's own search terms
(`queries` in the answer), after a Search with the words typed; keyword
searches only, never another AI call. This drives the real page against a
read-only index and checks:

  - the server: /api/search with `also=` searches every term and merges them
    (a row found only by an `also` term is returned);
  - after an Ask (answered by the test with a canned payload -- nothing is
    sent to the server's /api/ask): one /api/search per value tab, each with
    the question as `q` and the AI's other terms as `also`; counts on the
    tabs; the answer stays on the left while a tab shows on the right;
  - exactly one /api/ask per question;
  - a Search fills the tabs with the typed words and no `also`;
  - a new car clears the counts;
  - a phone (375x812): the tabs are the bottom bar; tapping one opens the
    Document view on that tab, Answer goes back.

Anything that is not a GET, apart from the canned Ask, fails the run.
"""
from _paths import ROOT
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from test_ui_picks import start_server  # noqa: E402  (same folder)

FAILURES = []
QUESTION = "front axle tightening torques"
AI_TERMS = ["wheel bolt torque", "track rod end nut"]


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def build_sample():
    tmp = Path(tempfile.mkdtemp(prefix="ar_answer_tabs_"))
    env = {**os.environ, "PYTHONUTF8": "1", "AUTORUNDOWN_HOME": str(tmp / "home")}
    subprocess.run([sys.executable, "tools/make_sample_corpus.py"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    db = tmp / "index.db"
    subprocess.run([sys.executable, "ingest.py", "--archive", str(ROOT / "sample_corpus"),
                    "--index", str(db)], cwd=ROOT, env=env, check=True, capture_output=True)
    return db


def canned_answer():
    """What /api/ask returns, without the model: a found answer with no page
    and the planner's search terms -- all the tabs need."""
    return {"found": True, "answer": "[test] canned answer", "specs": [], "figures": [],
            "document_page": "", "source_pages": [], "caveats": "", "page_titles": {},
            "queries": AI_TERMS + [QUESTION], "dropped_citations": [], "other_vehicle_specs": [],
            "added_parts": [], "usage": {"calls": 0, "cost_usd": 0}}


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

    # -- the server: `also` terms are searched and merged ---------------------
    with urllib.request.urlopen(f"{base}/api/vehicles") as r:
        vehicles = json.load(r)
    w211 = next((v for v in vehicles if "211" in (v.get("model") or "")), vehicles[0])

    def api_search(**params):
        url = f"{base}/api/search?" + urlencode(params, doseq=True)
        with urllib.request.urlopen(url) as r:
            return json.load(r)
    only_q = api_search(doc_id=w211["doc_id"], q="zzzz nothing matches", tab="torque")
    with_also = api_search(doc_id=w211["doc_id"], q="zzzz nothing matches", tab="torque",
                           also=["front axle"])
    check("server: a term in `also` finds rows the question alone does not",
          not only_q and len(with_also) > 0, f"{len(only_q)} vs {len(with_also)}")
    check("server: each row says which term found it",
          all(r.get("query") for r in with_also))

    asks, searches, forbidden, errors = [], [], [], []

    def on_api(route, request):
        u = urlparse(request.url)
        if u.path == "/api/ask" and request.method == "POST":
            asks.append(request.post_data_json)
            return route.fulfill(json=canned_answer())
        if request.method != "GET":
            forbidden.append(f"{request.method} {u.path}")
            return route.abort()
        if u.path == "/api/search":
            searches.append(parse_qs(u.query))
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

        def new_page(**ctx):
            pg = browser.new_context(**ctx).new_page()
            pg.route("**/api/**", on_api)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base)
            pg.wait_for_function("vehicles.length > 0")
            return pg

        def pick(pg, text):
            pg.fill("#carInput", text)
            pg.wait_for_timeout(400)
            pg.locator("#carList .opt").first.click()
            pg.wait_for_timeout(500)

        def counts(pg):
            return pg.evaluate("""Object.fromEntries([...document.querySelectorAll('#resTabs [data-tab]')]
                .filter(b => !b.hidden).map(b => [b.dataset.tab, b.querySelector('.tcount')?.textContent ?? '']))""")

        def tab_searches():
            return {s["tab"][0]: s for s in searches if s.get("tab", ["workshop"])[0] != "workshop"}

        # -- desktop ----------------------------------------------------------
        pg = new_page(viewport={"width": 1280, "height": 800})
        pick(pg, w211.get("model") or "W211")
        check("desktop: the tabs are at the top of the right box",
              pg.evaluate("document.querySelector('#resTabs').parentElement.id") == "right")
        searches.clear()
        pg.fill("#q", QUESTION)
        pg.click("#askBtn")
        pg.wait_for_selector("#out .answer")
        pg.wait_for_function("[...document.querySelectorAll('#resTabs [data-tab=torque] .tcount')]"
                             ".some(c => !c.hidden)")
        ts = tab_searches()
        check("Ask: one paid request", len(asks) == 1, str(len(asks)))
        for t in ("torque", "capacity"):
            s = ts.get(t)
            check(f"Ask: the {t} tab searched the question with the AI's terms",
                  bool(s) and s["q"] == [QUESTION] and s.get("also") == AI_TERMS, str(s))
        c = counts(pg)
        check("Ask: the Torque tab shows a count", c.get("torque", "") not in ("",), str(c))
        pg.click("#resTabs [data-tab=torque]")
        check("a tab shows in the right box, the answer stays on the left",
              pg.locator("#rpTorque").is_visible() and pg.locator("#out .answer").is_visible()
              and not pg.locator("#srcout").is_visible())
        check("the Torque tab lists value lines", pg.locator("#rpTorque .vlines li").count() > 0)

        searches.clear()
        pg.fill("#q", "wheel bolts")
        pg.click("#searchBtn")
        pg.wait_for_timeout(1500)
        ts = tab_searches()
        check("Search: the tabs are searched with the typed words, no `also`",
              bool(ts) and all(s["q"] == ["wheel bolts"] and "also" not in s for s in ts.values()),
              str(ts))
        check("Search: no paid request", len(asks) == 1)

        other = next((v for v in vehicles if v["doc_id"] != w211["doc_id"] and v.get("model")), None)
        if other:
            pick(pg, other["model"])
            check("a new car clears the tabs' counts", all(v == "" for v in counts(pg).values()),
                  str(counts(pg)))

        # -- phone --------------------------------------------------------------
        ph = new_page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        ph.click("#carPill")
        pick(ph, w211.get("model") or "W211")
        ph.fill("#q", QUESTION)
        ph.click("#askBtn")
        ph.wait_for_selector("#out .answer")
        ph.wait_for_timeout(500)            # the bar steps aside while the keyboard is up
        check("phone: the tabs are the bottom bar, shown with the answer",
              ph.locator("#resTabs").is_visible()
              and ph.evaluate("document.querySelector('#resTabs').parentElement.id") != "right")
        ph.click("#resTabs [data-tab=torque]")
        ph.wait_for_timeout(300)
        check("phone: a tab opens the Document view on that tab",
              ph.evaluate("document.body.classList.contains('docview')")
              and ph.locator("#rpTorque").is_visible())
        ph.click("#segAnswer")
        ph.wait_for_timeout(300)
        check("phone: Answer goes back to the answer", ph.locator("#out .answer").is_visible())

        check("nothing but GETs and the canned Ask", not forbidden, str(forbidden))
        check("no page errors", not errors, str(errors[:3]))
        browser.close()
    srv.should_exit = True
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
