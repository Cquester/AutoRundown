"""Syntax-check the UI's inline JavaScript -- FREE, needs Node.js (`node`).

    python tools/check_js.py

The whole UI is autorundown/web/index.html; a syntax error anywhere in its
<script> stops every button working, and nothing on the Python side notices.
Each inline script is written to a temp .js file and run through
`node --check` (parse only, nothing executes).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted((ROOT / "autorundown" / "web").glob("*.html"))
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found: install Node.js to run this check")
        return 2
    fails = checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            for n, m in enumerate(SCRIPT_RE.finditer(html), 1):
                # pad with newlines so node's line numbers are the page's
                line = html.count("\n", 0, m.start(1))
                js = Path(tmp) / f"{page.stem}-{n}.js"
                js.write_text("\n" * line + m.group(1), encoding="utf-8")
                r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
                checked += 1
                where = f"{page.relative_to(ROOT)} script {n}"
                if r.returncode:
                    fails += 1
                    print(f"FAIL {where}\n{r.stderr.replace(str(js), str(page))}")
                else:
                    print(f"ok   {where} ({len(m.group(1).splitlines())} lines)")
    if not checked:
        print("no inline scripts found -- the pattern no longer matches the page")
        return 1
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
