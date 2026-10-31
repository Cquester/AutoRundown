"""UI check: the AI chat is a conversation; Keyword search shows no AI -- free.

    python tests/test_chat_ui.py                    # sample WIS index, built in a temp dir
    python tests/test_chat_ui.py --index sample.db --headed

The user (2026-10-30): the AI chat should read like a chat window -- you and
the AI, top to bottom -- and an earlier reply, tapped, brings its page and
tabs back up. Keyword search is free, so it shows no AI model and no spend;
neither tab has sample questions or a description any more. A microphone
fills the box from speech and an answer can be read aloud (the browser's
own speech; both stubbed here). This drives the real page against a
read-only index, answers each Ask with a canned payload, and checks:

  - Keyword search: no model menu, no spend, no examples or description;
    "include other trims/engines" and History are still there;
  - AI chat: no examples or description; the model menu and spend are there;
  - two questions read top to bottom, each with its reply; the second is a
    follow-up of the first (`chat` sent); the newest reply is the one shown;
  - the box sits under the chat on a PC;
  - tapping the first reply opens its page and fills the tabs with its terms,
    with no new /api/ask;
  - the microphone fills the box and sends nothing; Read aloud speaks the
    answer and its values;
  - a phone (375x812): the same chat, the box docked under it.

Anything that is not a GET, apart from the canned Asks, fails the run.
"""
from _paths import ROOT, exit_now  # noqa: F401
import argparse
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

from test_answer_tabs import build_sample  # noqa: E402  (same folder)
from test_ui_picks import start_server  # noqa: E402

FAILURES = []
QUESTIONS = ["front axle tightening torques", "and the wheel bolts?"]
TERMS = [["track rod end nut"], ["wheel bolt torque"]]
PAGES: list[str] = []             # two real pages of the sample corpus

# the browser's speech, stubbed: recognition "hears" a phrase, synthesis
# records what it was asked to say
SPEECH_STUB = """
window.__spoken = [];
class FakeRec {
  start() { setTimeout(() => {
    this.onresult && this.onresult({results: [[{transcript: 'oil drain plug torque'}]]});
    this.onend && this.onend(); }, 50); }
  stop() { this.onend && this.onend(); }
}
window.SpeechRecognition = FakeRec;
Object.defineProperty(window, 'speechSynthesis', {configurable: true, value: {
  speak(u) { window.__spoken.push(u.text); }, cancel() {} }});
"""


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def canned(i):
    return {"found": True, "answer": f"[test] reply {i + 1}: 110 Nm.",
            "specs": [{"name": "Wheel bolt", "value": "110 Nm", "applicability": "",
                       "source_pages": [PAGES[i]]}],
            "figures": [], "document_page": PAGES[i], "source_pages": [PAGES[i]], "caveats": "",
            "page_titles": {}, "queries": TERMS[i] + [QUESTIONS[i]], "dropped_citations": [],
            "other_vehicle_specs": [], "added_parts": [], "chat_id": 7,
            "usage": {"calls": 2, "cost_usd": 0.012, "model": "claude-haiku-4-5"}}


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

    with urllib.request.urlopen(f"{base}/api/vehicles") as r:
        vehicles = json.load(r)
    w211 = next((v for v in vehicles if "211" in (v.get("model") or "")), vehicles[0])
    with urllib.request.urlopen(f"{base}/api/search?" + urlencode(
            {"doc_id": w211["doc_id"], "q": QUESTIONS[0]})) as r:
        PAGES[:] = list(dict.fromkeys(str(x["page_no"]) for x in json.load(r)))[:2]
    check("sample: two pages to cite", len(PAGES) == 2, str(PAGES))
    if len(PAGES) < 2:
        return 1

    asks, pages, forbidden, errors = [], [], [], []
    key = [True]

    def on_api(route, request):
        u = urlparse(request.url)
        if u.path == "/api/ask" and request.method == "POST":
            asks.append(request.post_data_json)
            return route.fulfill(json=canned(min(len(asks) - 1, 1)))
        if u.path == "/api/me":     # the test server has no key: Ask on unless a test says
            me = {**route.fetch().json(), "can_ask": key[0]}
            if not key[0]:
                me["ask_off"] = "No AI API key yet. Add one in Shop setup → AI answers."
            return route.fulfill(json=me)
        if request.method != "GET":
            forbidden.append(f"{request.method} {u.path}")
            return route.abort()
        if u.path.startswith("/api/page/"):
            pages.append(u.path.rsplit("/", 1)[-1])
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
            pg.add_init_script(SPEECH_STUB)
            pg.route("**/api/**", on_api)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base)
            pg.wait_for_function("vehicles.length > 0")
            if ctx["viewport"]["width"] <= 700:
                pg.click("#carPill")            # a phone: the car box is in the car sheet
            pg.fill("#carInput", w211.get("model") or "W211")
            pg.wait_for_timeout(400)
            pg.locator("#carList .opt").first.click()
            pg.wait_for_timeout(500)
            return pg

        def ask(pg, q, n):
            pg.fill("#q", q)
            pg.click("#askBtn")
            pg.wait_for_function(f"document.querySelectorAll('#out .areply').length === {n}")
            pg.wait_for_timeout(300)

        # -- desktop: the two tabs before a question -------------------------
        pg = new_page(viewport={"width": 1280, "height": 800})
        pg.click("#modeTabs [data-mode=kw]")
        check("Keyword search: no AI model menu", not pg.locator("#model").is_visible())
        check("Keyword search: no spend", not pg.locator("#cost").is_visible())
        check("Keyword search: other trims/engines and History stay",
              pg.locator("#variants").is_visible() and pg.locator("#histBtn").is_visible())
        check("Keyword search: no sample questions or description",
              pg.locator("[data-example], .examples").count() == 0
              and pg.inner_text("#outKw").strip() == "", pg.inner_text("#outKw"))
        pg.click("#modeTabs [data-mode=ai]")
        check("AI chat: the model menu and spend are shown",
              pg.locator("#model").is_visible() and pg.locator("#cost").is_visible())
        check("AI chat: no sample questions or description",
              pg.locator("[data-example], .examples").count() == 0
              and pg.inner_text("#out").strip() == "", pg.inner_text("#out"))
        check("AI chat: the box sits under the chat",
              pg.evaluate("document.querySelector('.askbar').getBoundingClientRect().top"
                          " >= document.querySelector('#out').getBoundingClientRect().bottom - 1"))

        # -- the microphone: fills the box, sends nothing --------------------
        mic = pg.locator("#micBtn")
        check("the microphone is offered", mic.is_visible())
        pg.fill("#q", "")
        mic.click()
        pg.wait_for_function("document.querySelector('#q').value.length > 0")
        check("the microphone fills the box", pg.input_value("#q") == "oil drain plug torque",
              pg.input_value("#q"))
        check("  ... and sends nothing", not asks, str(asks))

        # -- a conversation ---------------------------------------------------
        ask(pg, QUESTIONS[0], 1)
        ask(pg, QUESTIONS[1], 2)
        check("two Asks, the second a follow-up of the first",
              len(asks) == 2 and asks[0].get("chat") is None and asks[1].get("chat") == 7,
              str([a.get("chat") for a in asks]))
        qs = pg.locator("#out .turn .tq").all_inner_texts()
        check("the chat reads top to bottom: question, reply, question, reply",
              qs == QUESTIONS and pg.evaluate("""(() => {
                const t = [...document.querySelectorAll('#out .turn')];
                return t.length === 2 && t[0].getBoundingClientRect().top < t[1].getBoundingClientRect().top
                  && t.every(x => x.querySelector('.tq').getBoundingClientRect().top
                                  < x.querySelector('.areply').getBoundingClientRect().top);
              })()"""), str(qs))
        check("each reply is shown in full",
              pg.locator("#out .areply .answer").all_inner_texts()
              == ["[test] reply 1: 110 Nm.", "[test] reply 2: 110 Nm."])
        check("the newest reply is the one shown, its page opened",
              pg.locator("#out .areply.sel").get_attribute("data-turn") == "1"
              and pages and pages[-1] == PAGES[1], str(pages))
        check("the spend is counted", "$0.0240" in pg.inner_text("#cost"), pg.inner_text("#cost"))
        check("no description under the chat",
              "Follow up in the box" not in pg.inner_text("#out"))

        # -- an earlier reply, tapped ----------------------------------------
        pages.clear()
        pg.locator("#out .areply[data-turn='0'] .turnshow").click()
        pg.wait_for_timeout(800)
        check("tapping the first reply makes it the one shown",
              pg.locator("#out .areply.sel").get_attribute("data-turn") == "0")
        check("  ... opens its page", pages == [PAGES[0]], str(pages))
        tq = pg.evaluate("tabQuery")
        check("  ... fills the tabs from its own question, search terms and page",
              tq["q"] == QUESTIONS[0] and tq["also"] == TERMS[0] and tq["pages"] == [PAGES[0]], str(tq))
        check("  ... with no new AI call", len(asks) == 2, str(len(asks)))
        pg.locator("#out .areply[data-turn='1'] .answer").click()
        pg.wait_for_timeout(500)
        check("a tap anywhere on a reply shows it too",
              pg.locator("#out .areply.sel").get_attribute("data-turn") == "1")

        # -- read aloud -------------------------------------------------------
        pg.locator("#out .areply[data-turn='0'] [data-speak]").click()
        spoken = pg.evaluate("window.__spoken")
        check("Read aloud says the answer and its values",
              spoken == ["[test] reply 1: 110 Nm.. Wheel bolt: 110 Nm"], str(spoken))

        # -- Keyword search after the chat: still no AI ----------------------
        pg.click("#modeTabs [data-mode=kw]")
        check("Keyword search after a chat: still no model or spend",
              not pg.locator("#model").is_visible() and not pg.locator("#cost").is_visible())
        pg.click("#modeTabs [data-mode=ai]")
        check("back on AI chat the conversation is still there",
              pg.locator("#out .areply").count() == 2)

        # -- phone ------------------------------------------------------------
        ph = new_page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        ask(ph, QUESTIONS[0], 1)
        ask(ph, QUESTIONS[1], 2)
        check("phone: the chat reads top to bottom",
              ph.locator("#out .turn .tq").all_inner_texts() == QUESTIONS)
        check("phone: the box is docked under the chat",
              ph.evaluate("document.querySelector('.askbar').getBoundingClientRect().top"
                          " >= document.querySelector('#out').getBoundingClientRect().bottom - 1"))

        # -- no API key: greyed out with why, never a question that spins ----
        key[0] = False
        n = len(asks)
        for nk in (new_page(viewport={"width": 1280, "height": 800}),
                   new_page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)):
            w = nk.viewport_size["width"]
            check(f"no key ({w}px): Ask and the box are greyed out",
                  nk.locator("#askBtn").is_disabled() and nk.locator("#q").is_disabled())
            check(f"no key ({w}px): the AI chat says why, with the way out",
                  nk.locator("#keyNote").is_visible() and "API key" in nk.inner_text("#keyNote")
                  and nk.locator("#keyNoteAdd").is_visible() and nk.locator("#keyNoteKw").is_visible())
            nk.evaluate("$('#q').disabled = false; $('#q').value = 'drain plug torque'; ask()")
            nk.wait_for_timeout(500)
            check(f"no key ({w}px): Enter or Ask sends nothing, no spinner",
                  len(asks) == n and nk.locator("#asking").count() == 0)
            nk.click("#keyNoteKw")
            check(f"no key ({w}px): Keyword search still works from the note",
                  nk.locator("#searchBtn").is_visible() and nk.locator("#q").is_enabled()
                  and not nk.locator("#keyNote").is_visible())
        browser.close()

    check("no request but GETs and the canned Asks", not forbidden, str(forbidden))
    check("no page errors", not errors, str(errors[:3]))
    srv.should_exit = True
    print(f"\n{'all passed' if not FAILURES else f'{len(FAILURES)} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    exit_now(main())
