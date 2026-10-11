"""UI check: the variant the page sends is the car it shows -- free.

    python test_ui_picks.py                       # the real index.db, headless
    python test_ui_picks.py --index sample_wis.db --headed

sample_wis.db (make_sample_corpus.py + ingest.py) carries ~20 hand-made
variants and two VIN prefixes, enough for CI (ci.yml runs it there); the real
index checks ~1,600 variants.

Needs `pip install -r requirements-dev.txt` (Playwright). It drives the
installed Edge or Chrome (`channel=msedge`), so no browser download.

Why it exists: the page used to sort each generation's variants in place and
send the *sorted* position as `variant=`; the server indexes the stored order,
so picking "E 500/550 4MATIC" searched the E 63 AMG (CLAUDE.md, "Fixed
2026-10-08"). The server-side evals can't see that -- the index is computed
in the browser. This loads the real page against the real index, makes picks
the ways a user does, captures every /api/search request, and checks the
index against the server's own `vehicles.variants`, read from the index here,
not from the page:

  - one-line box: real typing + arrow/Enter on a few queries (incl. a US VIN
    and a European FIN), then EVERY suggestion the box can produce
    (`carIndex`) through `applyCar`, the function its click handler calls;
  - Browse: every option of every generation's Variant menu, picked with a
    real `change` event, plus "Any <engine>" options;
  - the VIN dialog, and one VIN prefix per variant that has one;
  - a reload: the remembered pick (`autorundown.vehicle.v3`) still names
    the same car.

The index is opened READ-ONLY (sqlite `mode=ro`) and /api/search is answered
by the test, not the server, so nothing is written and it is fast. Anything
that is not a GET, and /api/ask in particular, is aborted and fails the run --
this never spends API credit.
"""
import argparse
import json
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autorundown import config, index  # noqa: E402
from autorundown.search import scope_ids  # noqa: E402

FAILURES = []
MAX_FAIL_LINES = 25


def fail(msg):
    if len(FAILURES) < MAX_FAIL_LINES:
        print(f"  FAIL {msg}")
    FAILURES.append(msg)


def ro_connect(path, setup=False):
    """index.connect() runs the schema + migrations, i.e. writes. Not here,
    whatever `setup` the server asks for."""
    p = quote(Path(path).resolve().as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def years_in(s):
    out = set()
    for part in str(s or "").split(","):
        a, _, b = part.partition("-")
        if a.strip().isdigit():
            out.update(range(int(a), int(b or a) + 1))
    return out


def start_server(index_path):
    import uvicorn
    from autorundown import server
    index.connect = ro_connect          # server.py calls index.connect per request
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(index_path), host="127.0.0.1",
                                        port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(200):
        if srv.started:
            return srv, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise SystemExit("server did not start")


class Truth:
    """The server's view: vehicles.variants exactly as stored in the index."""

    def __init__(self, index_path):
        conn = ro_connect(index_path)
        self.vehicles = {v["doc_id"]: v for v in index.vehicles(conn)}
        conn.close()
        self.variants = {d: json.loads(v.get("variants") or "[]")
                         for d, v in self.vehicles.items()}

    def variant(self, doc, params, what):
        """The stored variant the request's `variant=` names, or None + FAIL."""
        if "variant" not in params:
            fail(f"{what}: no variant= sent")
            return None
        n = int(params["variant"])
        vs = self.variants.get(doc, [])
        if not 0 <= n < len(vs):
            fail(f"{what}: variant={n} out of range for {doc} ({len(vs)} variants)")
            return None
        return vs[n]


def check_scope(truth, doc, p, what):
    """What the server would search for this request: the car first, and the
    picked variant's engine buckets (only the chosen engine's, if one is)."""
    v = truth.vehicles[doc]
    n = int(p["variant"]) if "variant" in p else None
    scope = scope_ids(v, n, p.get("engine"))
    if scope[0] != doc:
        fail(f"{what}: scope starts with {scope[0]}, not {doc}")
    if n is not None:
        fams = [f for f in (p.get("engine") or "").split(",") if f]
        for eid in truth.variants[doc][n].get("engine_ids", []):
            if (not fams or any(eid.lower().endswith(f"-e{f}") for f in fams)) and eid not in scope:
                fail(f"{what}: engine bucket {eid} missing from scope")


def check_engine(x, p, what):
    fams = [f for f in (p.get("engine") or "").split(",") if f]
    bad = [f for f in fams if f not in (x.get("engine_families") or {})]
    if bad:
        fail(f"{what}: engine={p['engine']} but {x['name']} has {sorted(x.get('engine_families') or {})}")


# -- the in-page drivers ------------------------------------------------------
# Each pick sets #q to a unique tag and awaits keywordSearch(), so every
# captured request is paired with what the page showed at that moment.

JS_ONE_LINE = """async () => {
  const out = [];
  for (let k = 0; k < carIndex.length; k++) {
    const e = carIndex[k];
    applyCar(e);
    await new Promise(r => setTimeout(r, 0));
    $('#q').value = 'oneline-' + k;
    await keywordSearch();
    const opt = $('#variant').hidden ? null : $('#variant').selectedOptions[0];
    out.push({tag: 'oneline-' + k, label: e.label, sub: e.sub, doc: e.doc, year: e.year,
              variant: e.variant, generation: !!e.generation,
              shown: $('#carInput').value, browse: opt ? opt.textContent.trim() : null,
              browseDoc: $('#vehicle').value});
  }
  return out;
}"""

JS_BROWSE = """async () => {
  const out = []; let k = 0;
  $('#year').value = ''; $('#make').value = '';
  $('#year').dispatchEvent(new Event('change'));
  const docs = vehicles.filter(v => v._variants.length).map(v => v.doc_id);
  for (const doc of docs) {
    $('#vehicle').value = doc;
    $('#vehicle').dispatchEvent(new Event('change'));
    for (const o of [...$('#variant').options]) {
      if (!o.value) continue;
      $('#variant').value = o.value;
      $('#variant').dispatchEvent(new Event('change'));
      await new Promise(r => setTimeout(r, 0));
      const tag = 'browse-' + (k++);
      $('#q').value = tag;
      await keywordSearch();
      out.push({tag, doc, value: o.value, text: o.textContent.trim(), title: o.title || '',
                shown: $('#carInput').value, scope: $('#scopeNote').textContent,
                model: $('#vehicle').selectedOptions[0].textContent});
    }
  }
  return out;
}"""

JS_VIN = """async () => {
  const out = []; let k = 0;
  for (const v of vehicles) for (const x of v._variants) {
    const vin9 = (x.vin9 || [])[0];
    if (!vin9) continue;
    const vin = vin9 + 'H' + 'F123456';          // position 10 'H': 1987 / 2017
    const e = carFromVin(vin);
    if (e.error) { out.push({vin, error: e.error}); continue; }
    applyCar(e);
    const tag = 'vin-' + (k++);
    $('#q').value = tag;
    await keywordSearch();
    out.push({tag, vin, label: e.label, doc: e.doc, year: e.year, shown: $('#carInput').value});
  }
  return out;
}"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--index", default=config.load().index_path)
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("needs Playwright: pip install -r requirements-dev.txt")
    if not Path(args.index).exists():
        raise SystemExit(f"no index at {args.index}")

    truth = Truth(args.index)
    if not any(truth.variants.values()):
        raise SystemExit(f"{args.index} has no vehicle variants -- nothing to check "
                         "(a WIS index built with the current importer is needed)")
    print(f"index: {args.index} (read-only), {len(truth.vehicles)} vehicles, "
          f"{sum(map(len, truth.variants.values()))} variants")
    srv, base = start_server(args.index)

    sent, forbidden = {}, []

    def on_api(route, request):
        u = urlparse(request.url)
        if request.method != "GET" or u.path == "/api/ask":
            forbidden.append(f"{request.method} {u.path}")
            return route.abort()
        if u.path == "/api/search":
            p = {k: v[0] for k, v in parse_qs(u.query).items()}
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
        page = browser.new_page()
        page.route("**/api/**", on_api)
        page.on("pageerror", lambda e: fail(f"page error: {e}"))
        page.goto(base)
        page.wait_for_function("vehicles.length > 0 && carIndex.length > 0")

        def search(tag):
            page.fill("#q", tag)
            with page.expect_response(lambda r: "/api/search?" in r.url
                                      and f"q={tag}&" in r.url):
                page.click("#searchBtn")
            p = sent.get(tag)
            if p is None:
                fail(f"{tag}: no /api/search request captured")
            return p

        def expect_variant(p, tag, name=None, bm=None, label=None):
            x = truth.variant(p["doc_id"], p, tag)
            if x is None:
                return
            if name and x["name"] != name:
                fail(f"{tag}: sent variant {p['variant']} = {x['name']!r}, shown {name!r}")
            if bm and bm not in x["baumuster"]:
                fail(f"{tag}: sent variant {x['name']} {x['baumuster']}, expected Baumuster {bm}")
            if label and not label.endswith(x["name"]):
                fail(f"{tag}: label {label!r} but sent variant is {x['name']!r}")
            check_engine(x, p, tag)
            check_scope(truth, p["doc_id"], p, tag)
            return x

        # 1. typed queries, as a user does: type, arrow down to each
        #    suggestion, Enter, Keyword search
        print("\n1. one-line box, typed")
        typed = [("2009 Mercedes-Benz E550 4Matic", "-211", "E 500/550 4MATIC"),
                 ("09 e550 4matic", "-211", "E 500/550 4MATIC"),
                 ("190e 2.3-16", "-201", "190 E 2.3 16"),
                 ("1987 190e 2.3-16", "-201", "190 E 2.3 16"),
                 ("2010 e350", None, None), ("c300 4matic", None, None),
                 ("2012 ml350", None, None), ("300e", None, None)]
        n_typed = 0
        for q, want_doc, want_name in typed:
            page.wait_for_timeout(200)
            page.fill("#carInput", "")
            page.type("#carInput", q)
            labels = page.eval_on_selector_all("#carList .opt[data-k] > div:first-child",
                                               "els => els.map(e => e.textContent)")
            if not labels:
                fail(f"typed {q!r}: no suggestions")
                continue
            for k, label in enumerate(labels):
                # the box hides its list 150 ms after a blur (the search click);
                # typing faster than that has the list vanish under Enter
                page.wait_for_timeout(200)
                page.fill("#carInput", "")
                page.type("#carInput", q)
                page.wait_for_selector("#carList:not([hidden])")
                for _ in range(k):                  # the first is already highlighted
                    page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                tag = f"typed-{n_typed}"
                n_typed += 1
                p = search(tag)
                if not p:
                    continue
                if page.input_value("#carInput") != label:
                    fail(f"{tag}: box shows {page.input_value('#carInput')!r}, picked {label!r}")
                if k == 0 and want_doc and not p["doc_id"].endswith(want_doc):
                    fail(f"typed {q!r}: top hit searched {p['doc_id']}, expected *{want_doc}")
                if "variant" in p:
                    x = expect_variant(p, f"typed {q!r} #{k}", label=label)
                    if k == 0 and want_name and x and x["name"] != want_name:
                        fail(f"typed {q!r}: top hit is {x['name']!r}, expected {want_name!r}")
                elif not page.evaluate("carPick && /whole generation/.test(carPick.sub || '')") \
                        and truth.variants.get(p["doc_id"]):
                    fail(f"typed {q!r} #{k} {label!r}: no variant sent for a named car")
        print(f"   {n_typed} picks from {len(typed)} queries")

        # 2. VINs typed into the same box, and through the VIN dialog
        print("\n2. VINs")
        page.click("#browseBtn")            # the VIN button sits in the Browse row
        for vin, bm, name in [("WDBDA34D4HF123456", "201.034", "190 E 2.3 16"),
                              ("WDB2010341F123456", "201.034", "190 E 2.3 16")]:
            page.wait_for_timeout(200)
            page.fill("#carInput", "")
            page.type("#carInput", vin)
            page.wait_for_selector("#carList:not([hidden])")
            page.keyboard.press("Enter")
            p = search(f"vinbox-{vin}")
            if p:
                expect_variant(p, f"VIN box {vin}", name=name, bm=bm,
                               label=page.input_value("#carInput"))
            if page.is_hidden("#picker"):   # choosing a car closes the Browse row
                page.click("#browseBtn")
            page.click("#vinBtn")
            page.fill("#vinInput", vin)
            page.click("#vinFind")
            page.wait_for_function("!document.querySelector('#vinDlg').open", timeout=5000)
            p = search(f"vindlg-{vin}")
            if p:
                expect_variant(p, f"VIN dialog {vin}", name=name, bm=bm)
        print("   2 VINs x (box, dialog)")

        # 3. Browse dropdowns with real select_option, then a reload: the
        #    remembered pick must still name the same car
        print("\n3. Browse + reload")
        doc211 = next((d for d in truth.vehicles if d.endswith("-211")), None)
        if doc211:
            if page.is_hidden("#picker"):
                page.click("#browseBtn")
            page.select_option("#year", "")
            page.select_option("#make", "")
            page.select_option("#vehicle", doc211)
            n = next(i for i, x in enumerate(truth.variants[doc211])
                     if x["name"] == "E 500/550 4MATIC")
            page.select_option("#variant", str(n))
            p = search("browse-e550")
            if p:
                expect_variant(p, "Browse E 500/550 4MATIC", name="E 500/550 4MATIC")
            page.reload()
            page.wait_for_function("vehicles.length > 0 && carIndex.length > 0")
            p = search("reload-e550")
            if p:
                expect_variant(p, "after reload", name="E 500/550 4MATIC")
            print("   W211 E 500/550 4MATIC: picked, searched, reloaded, searched")

        # 4. exhaustive, in-page: every one-line suggestion
        print("\n4. every one-line suggestion (applyCar)")
        t0 = time.time()
        rows = page.evaluate(JS_ONE_LINE)
        for r in rows:
            p, what = sent.get(r["tag"]), f"{r['label']!r} ({r['doc']})"
            if p is None:
                fail(f"{what}: no request")
                continue
            if p["doc_id"] != r["doc"] or r["browseDoc"] != r["doc"]:
                fail(f"{what}: searched {p['doc_id']}, Browse shows {r['browseDoc']}")
            if r["shown"] != r["label"]:
                fail(f"{what}: box shows {r['shown']!r}")
            if r["variant"] is None:
                if "variant" in p:
                    fail(f"{what}: variant={p['variant']} sent for a whole-generation pick")
                continue
            x = truth.variant(p["doc_id"], p, what)
            if x is None:
                continue
            make = truth.vehicles[p["doc_id"]]["make"] or ""
            want = ([f"{r['year']} {make} {x['name']}"] if r["year"]
                    else [f"{make} {x['name']}"])
            if r["label"] not in want:
                fail(f"{what}: sent variant {p['variant']} = {x['name']!r}")
            if r["year"] and r["year"] not in set().union(
                    *map(years_in, (x.get("us_years") or {}).values())):
                fail(f"{what}: {x['name']} has no US year {r['year']}")
            if r["browse"] != x["name"]:
                fail(f"{what}: Browse menu shows {r['browse']!r}, sent {x['name']!r}")
            check_engine(x, p, what)
            check_scope(truth, p["doc_id"], p, what)
        print(f"   {len(rows)} picks, {sum(r['variant'] is not None for r in rows)} with a "
              f"variant ({time.time() - t0:.0f} s)")

        # 5. exhaustive, in-page: every Browse Variant option
        print("\n5. every Browse variant option")
        t0 = time.time()
        rows = page.evaluate(JS_BROWSE)
        n_var = n_eng = 0
        for r in rows:
            p, what = sent.get(r["tag"]), f"Browse {r['doc']} {r['text']!r}"
            if p is None:
                fail(f"{what}: no request")
                continue
            if p["doc_id"] != r["doc"]:
                fail(f"{what}: searched {p['doc_id']}")
            if r["value"].startswith("e:"):
                n_eng += 1
                if "variant" in p or p.get("engine") != r["value"][2:]:
                    fail(f"{what}: sent variant={p.get('variant')} engine={p.get('engine')}")
                check_scope(truth, p["doc_id"], p, what)
                continue
            n_var += 1
            x = truth.variant(p["doc_id"], p, what)
            if x is None:
                continue
            v = truth.vehicles[p["doc_id"]]
            if r["text"] != x["name"] or r["title"] != ", ".join(x["baumuster"]):
                fail(f"{what} [{r['title']}]: sent variant {p['variant']} = "
                     f"{x['name']!r} [{', '.join(x['baumuster'])}]")
            if r["shown"] != f"{v['make']} {x['name']} ({v['model']})":
                fail(f"{what}: box shows {r['shown']!r}, sent {x['name']!r}")
            if not r["scope"].startswith(f"{x['name']} ({', '.join(x['baumuster'])})"):
                fail(f"{what}: scope note {r['scope'][:60]!r}")
            if "engine" in p:
                fail(f"{what}: engine={p['engine']} sent with a single variant")
            check_scope(truth, p["doc_id"], p, what)
        print(f"   {n_var} variant options, {n_eng} 'Any <engine>' options "
              f"({time.time() - t0:.0f} s)")

        # 6. exhaustive, in-page: one US VIN prefix per variant that has one
        print("\n6. one VIN per variant with a VIN prefix (carFromVin)")
        rows = page.evaluate(JS_VIN)
        for r in rows:
            if "error" in r:
                fail(f"VIN {r['vin']}: {r['error']}")
                continue
            p = sent.get(r["tag"])
            if p is None:
                fail(f"VIN {r['vin']}: no request")
                continue
            x = truth.variant(p["doc_id"], p, f"VIN {r['vin']}")
            if x is None:
                continue
            if r["vin"][:9] not in (x.get("vin9") or []):
                fail(f"VIN {r['vin']} ({r['label']!r}): sent {x['name']!r}, whose VIN "
                     f"prefixes don't include {r['vin'][:9]}")
            if not r["label"].endswith(x["name"]) or r["shown"] != r["label"]:
                fail(f"VIN {r['vin']}: shows {r['shown']!r}, sent {x['name']!r}")
        print(f"   {len(rows)} VINs")

        browser.close()
    srv.should_exit = True

    for f in forbidden:
        fail(f"blocked a non-free/non-GET request: {f}")
    print(f"\n{len(sent)} searches captured, {len(FAILURES)} failure(s)"
          + (f" (first {MAX_FAIL_LINES} shown)" if len(FAILURES) > MAX_FAIL_LINES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
