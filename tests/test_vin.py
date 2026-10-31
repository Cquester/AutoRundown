"""VIN checks before decoding (index.html's vin-check block) -- FREE, ~2 s.

    python tests/test_vin.py

Runs the page's own pure VIN functions (between `vin-check:begin` and
`vin-check:end` in autorundown/web/index.html) in Node, or, without Node, in
the installed Edge/Chrome through Playwright. Checks:

  - the ISO 3779 check digit against an independent Python version, on
    known VINs and a few thousand random ones;
  - US-format VINs (the audit's harness inputs, made valid) pass, and every
    one-character typo that changes a character's value fails -- the guard
    against a typo naming another car with another engine;
  - European FINs are never checked (they have no check digit);
  - I/O/Q get their own message and the 1/0 fix, with the changed positions;
  - lengths other than 17 warn; spaces and hyphens are dropped;
  - vinFromText finds the VIN in OCR / barcode text (a leading "I", "VIN:",
    misread O's, a shifted window that passes by chance); vinVote combines
    several reads of one photo.
  - when Mercedes' VIN table is on this PC (read-only), every pair of its
    prefixes one character apart (the audit's typo pairs) is caught.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from _paths import ROOT, exit_now

HTML = (ROOT / "autorundown" / "web" / "index.html").read_text(encoding="utf-8")
# ../Mercedes Documents next to the repo (or next to the main checkout, from a
# worktree under .claude/worktrees/)
VIN2FIN = next((p / "Mercedes Documents" / "autorundown_out" / "wis_extract" / "vin2fin.json"
                for p in ROOT.parents
                if (p / "Mercedes Documents" / "autorundown_out" / "wis_extract" / "vin2fin.json").is_file()),
               ROOT.parent / "Mercedes Documents" / "autorundown_out" / "wis_extract" / "vin2fin.json")
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL", msg)


def block(name: str) -> str:
    m = re.search(rf"/\* {name}:begin.*?\*/(.*?)/\* {name}:end \*/", HTML, re.S)
    if not m:
        raise SystemExit(f"no {name} block in index.html")
    return m.group(1)


# -- an independent ISO 3779 check digit -------------------------------------
VAL = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9]))
VAL.update({str(d): d for d in range(10)})
WEIGHT = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
VIN_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def check_digit(vin: str) -> str:
    r = sum(VAL[c] * w for c, w in zip(vin, WEIGHT)) % 11
    return "X" if r == 10 else str(r)


def make_valid(prefix9: str, rest8: str) -> str:
    """A 17-character VIN from the first 9 characters (check digit included)
    and 8 more, the serial's last digits changed until position 9 is right.
    None when no serial does it."""
    for a in range(10):
        for b in range(10):
            vin = prefix9 + rest8[:6] + str(a) + str(b)
            if check_digit(vin) == prefix9[8]:
                return vin
    return None


def with_check(vin: str) -> str:
    return vin[:8] + check_digit(vin) + vin[9:]


# -- running the page's JavaScript -------------------------------------------
RUNNER = r"""
const out = [];
for (const [fn, args] of CASES) {
  try { out.push(globalThis[fn](...args)); } catch (e) { out.push({throw: String(e)}); }
}
OUT(JSON.stringify(out));
"""


def run_js(cases: list) -> list:
    code = block("vin-check") + "\n" + "\n".join(
        f"globalThis.{f} = {f};" for f in ("vinCheck", "vinCheckDigit", "vinFromText", "vinClean", "vinVote"))
    # vinFromText takes a maker test: the cases name it, the runner makes it
    code += "\nglobalThis.vinFromTextMB = t => vinFromText(t, w => /^(W[A-Z0-9]{2}|4JG|55S)$/.test(w));"
    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "vin.js"
            f.write_text(code + f"\nconst CASES = {json.dumps(cases)};\nconst OUT = s => process.stdout.write(s);\n"
                         + RUNNER, encoding="utf-8")
            r = subprocess.run([node, str(f)], capture_output=True, text=True, encoding="utf-8")
            if r.returncode:
                raise SystemExit("node failed:\n" + r.stderr)
            return json.loads(r.stdout)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            raise SystemExit("neither node nor a browser for Playwright found")
        page = browser.new_page()
        page.add_script_tag(content=code)
        out = page.evaluate("CASES => { let s; const OUT = x => { s = x; };" + RUNNER + " return s; }", cases)
        browser.close()
        return json.loads(out)


def main() -> int:
    rnd = random.Random(3779)

    # 1. check digit vs the Python version
    known = {"1M8GDM9AXKP042788": "X", "11111111111111111": "1"}
    randoms = ["".join(rnd.choice(VIN_ALPHABET) for _ in range(17)) for _ in range(3000)]
    cases = [["vinCheckDigit", [v]] for v in list(known) + randoms]
    got = run_js(cases)
    for v, g in zip(list(known) + randoms, got):
        want = known.get(v, check_digit(v))
        if g != want:
            fail(f"check digit of {v}: page says {g}, ISO 3779 says {want}")
    print(f"1. check digit: {len(cases)} VINs compared with ISO 3779")

    # 2. US-format VINs from the VIN audit's harness (.dev/qa/vin/near.py),
    # given a right check digit; every value-changing one-character typo
    us = ["WDBJF65J0YB123456", "WDBJF65J1YB123456", "WDBJF70J0YB123456", "WDBJF20F0XA123456",
          "WDBJH65J02B123456", "WDBJH82J02B123456", "WDBJF65J3TA123456", "WDBJF55F3TA123456",
          "WDBUF65J63A123456", "WDDHF8JB0CA123456", "WDDHF8HB0AA123456", "WDDHF5KB6CA123456",
          "WDBDA34D4HF123456", "4JGBB86E36A123456", "1FTFW1EF5FFA12345", "W1KZF8DB1LA123456"]
    valid = [with_check(v) for v in us]
    typos = []
    for v in valid:
        for i in range(17):
            if i == 8:
                continue
            for c in VIN_ALPHABET:
                if c != v[i] and VAL[c] != VAL[v[i]]:
                    typos.append(v[:i] + c + v[i + 1:])
    # a typo in the check digit itself
    typos += [v[:8] + ("0" if v[8] != "0" else "1") + v[9:] for v in valid]
    rows = run_js([["vinCheck", [v]] for v in valid + typos])
    for v, r in zip(valid, rows[:len(valid)]):
        if r.get("check") != "ok" or r.get("warn") or r.get("error"):
            fail(f"valid US VIN {v}: {r}")
    missed = [v for v, r in zip(typos, rows[len(valid):]) if r.get("check") != "bad"]
    # a typo that puts I/O/Q in is refused outright instead (error), which is
    # also right; anything else must be 'bad'
    missed = [v for v in missed if not re.search("[IOQ]", v)]
    for v in missed[:10]:
        fail(f"typo {v} passed the check digit")
    bad_msg = rows[len(valid)].get("warn") or ""
    if "check digit doesn't match" not in bad_msg.lower():
        fail(f"wrong-check-digit message: {bad_msg!r}")
    print(f"2. {len(valid)} valid US VINs pass; {len(typos)} one-character typos, "
          f"{len(typos) - len(missed)} caught")

    # 3. FINs: no check digit, never warned
    fins = ["WDB2100651A123456", "WDB2100701A123456", "WDB2102821A123456", "WDB2010341F123456",
            "WDC1641861A123456", "W1K2130041A123456", "4JG1641861A123456"]
    for v, r in zip(fins, run_js([["vinCheck", [v]] for v in fins])):
        if r.get("check") != "na" or r.get("warn") or r.get("error"):
            fail(f"FIN {v} was checked: {r}")
    print(f"3. {len(fins)} FINs: no check digit applied")

    # 4. I/O/Q, length, spaces and hyphens
    io = run_js([["vinCheck", ["WDBJF65JOYB123456"]], ["vinCheck", ["wdbjf65jqyb12345i"]],
                 ["vinCheck", ["WDBJF65J0YB12345"]], ["vinCheck", ["WDBJF65J0YB1234567"]],
                 ["vinCheck", [" WDB-JF65J 0YB-123456 "]], ["vinClean", ["wdb jf65-j0.yb"]]])
    r = io[0]
    if not (r["error"] and 'position 9 is "O"' in r["error"] and "O → 0" in r["error"]
            and r["fixed"] == "WDBJF65J0YB123456" and r["changed"] == [8]):
        fail(f"I/O/Q message or fix: {r}")
    r = io[1]
    if not (r["fixed"] == "WDBJF65J0YB123451" and r["changed"] == [8, 16]):
        fail(f"Q and I fixed: {r}")
    if "16 characters" not in (io[2]["warn"] or "") or io[2]["check"] != "na":
        fail(f"short VIN: {io[2]}")
    if "18 characters" not in (io[3]["warn"] or ""):
        fail(f"long VIN: {io[3]}")
    if io[4]["vin"] != "WDBJF65J0YB123456":
        fail(f"spaces/hyphens: {io[4]}")
    if io[5] != "WDBJF65J0YB":
        fail(f"vinClean: {io[5]}")
    print("4. I/O/Q, length and separators")

    # 5. finding the VIN in read text
    v = with_check("WDBJF65J0YB123456")
    fin = "WDB2100651A123456"
    texts = [
        (f"I{v}", v, "Code 39 with the import 'I'"),
        (f"I{fin}", fin, "Code 39 FIN with an 'I'"),
        (f"MERCEDES-BENZ AG\nVIN: {v[:8]}{v[8]} {v[9:]}\nGVWR 2490 KG", v, "label text"),
        (f"FIN {fin.replace('0', 'O', 1)}", fin, "FIN with O for 0"),
        (f"XX{v}YY", v, "glued to other text"),
        ("NO VIN HERE 12345", None, "nothing"),
    ]
    got = run_js([["vinFromTextMB", [t]] for t, _, _ in texts])
    for (t, want, what), g in zip(texts, got):
        top = g[0]["vin"] if g else None
        if top != want:
            fail(f"vinFromText ({what}): {top!r}, want {want!r}")
    if not got[3] or got[3][0]["changed"] != [5]:
        fail(f"vinFromText should mark the O it read as 0: {got[3][:1]}")
    print(f"5. vinFromText: {len(texts)} texts")

    # 5b. several reads of one photo: a right check digit wins; otherwise
    # the majority per character, disagreements marked
    def rd(vin, check="na", changed=()):
        return {"vin": vin, "raw": vin, "changed": list(changed), "check": check, "score": 4}
    good = with_check("WDBJF65J0YB123456")
    typo = good[:12] + ("8" if good[12] != "8" else "9") + good[13:]
    votes = run_js([
        ["vinVote", [[rd(typo, "bad"), rd(good, "ok")]]],
        ["vinVote", [[rd("WDB1641512F701959"), rd("WDB1641612F701959"), rd("WDB1641512F701969")]]],
        ["vinVote", [[rd("WDB1641512F701959")]]],
    ])
    if votes[0]["vin"] != good or votes[0]["unsure"]:
        fail(f"vote: the read with a right check digit should win: {votes[0]}")
    if votes[1]["vin"] != "WDB1641512F701959" or votes[1]["unsure"] != [7, 15]:
        fail(f"vote: majority per character, unsure [7, 15]: {votes[1]}")
    if votes[2]["vin"] != "WDB1641512F701959" or votes[2]["unsure"]:
        fail(f"vote: one read: {votes[2]}")
    print("5b. vinVote")

    # 6. the audit's typo pairs, from Mercedes' own table when on this PC
    if VIN2FIN.is_file():
        # US-format prefixes only: ~230 entries (8AB 384.067 …) are FINs of
        # other markets, Baumuster at 4-9, no check digit
        keys = sorted(k for k in json.loads(VIN2FIN.read_text(encoding="utf-8"))
                      if not k[3:6].isdigit())
        first = {}                        # 8 characters -> one full prefix (9)
        for k in keys:
            first.setdefault(k[:8], k)
        index = {}
        for p8 in first:
            for i in range(8):
                index.setdefault(p8[:i] + "*" + p8[i + 1:], []).append(p8)
        pairs = [(first[a], b) for group in index.values() for a in group for b in group if a != b]
        # a's real VIN (valid), mistyped in one of positions 1-8 into b's
        # first 8 characters, keeping a's check digit
        cases, pairs_used = [], []
        for a, b in pairs:
            real = make_valid(a, "YB123456")
            if real:
                cases.append(["vinCheck", [b[:8] + real[8:]]])
                pairs_used.append((a, b))
        rows = run_js(cases)
        slipped = [(p, r) for p, r in zip(pairs_used, rows) if r.get("check") != "bad"]
        print(f"6. Mercedes' VIN table: {len(keys)} prefixes, {len(pairs_used)} one-character typo "
              f"pairs, {len(pairs_used) - len(slipped)} caught by the check digit")
        # same value at that position (A/J/1, B/K/S/2 …) is the one typo
        # ISO 3779 can't see; anything else slipping through is a bug
        for (a, b), r in slipped:
            i = next(n for n in range(8) if a[n] != b[n])
            if VAL.get(a[i]) != VAL.get(b[i]):
                fail(f"typo {a[:8]} -> {b[:8]} not caught: {r}")
    else:
        print("6. Mercedes' VIN table not on this PC: skipped")

    print("\nFAILED" if FAILS else "\nALL PASS", f"({len(FAILS)} failures)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    exit_now(main())
