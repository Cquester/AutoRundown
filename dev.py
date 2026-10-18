"""Start a throwaway AutoRundown on this PC to try a change -- free, and
nothing it does touches your real index, settings, accounts or spend log.

    python dev.py            # sample data, http://localhost:8757
    python dev.py --empty    # no index: the first-start import dialog
    python dev.py --reset    # delete .dev/ and build the samples again
    python dev.py --fake-api # Ask answers from a fake Anthropic API (free)
    python dev.py --fake-vm  # Mercedes VM tab runs tools/fake_extractor (free)

Everything lives in `.dev/` in this repo (gitignored): its own
AUTORUNDOWN_HOME (config, sign-in accounts, TLS), its own index. The samples
are invented text (tools/make_sample_lemon.py, tools/make_sample_corpus.py): two of the
three LEMON sample vehicles are left unimported, so the LEMON tab's folder import
source has something to pick. Port 8757, localhost only by default, so it can
run next to the real one (8756) and isn't on the shop network.

No API key is copied in: Ask stays off unless you add one in Settings, which
would spend real money -- keyword search, the picker, the page view and
imports are all free. With --fake-api, Ask runs its whole real path against
tools/fake_anthropic.py on port+1 instead, for every AI provider (Anthropic,
Gemini, xAI, DeepSeek): nothing is sent to any of them, even if a real key is
typed into Shop setup (every provider's API address is the fake's). Only the
Anthropic key is preset; type any dummy key for the others.

With --fake-vm, the extractor folder is set to tools/fake_extractor: the
Mercedes VM tab lists pretend VMs and its jobs run a stand-in vm_pipeline.py
(progress lines, a tiny corpus in .dev/fake_vm_out) -- no VirtualBox, no
database. The pause between the two parts of a WIS + EPC run is 5 s here.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV = ROOT / ".dev"


def run(cmd: list[str], env: dict) -> None:
    print("$", " ".join(cmd[1:]), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def build(index: Path, env: dict) -> None:
    py = sys.executable
    lemon = DEV / "lemon"
    run([py, "tools/make_sample_lemon.py", "--out", str(lemon)], env)
    run([py, "tools/make_sample_corpus.py"], env)
    run([py, "ingest.py", "--archive", str(ROOT / "sample_corpus"), "--index", str(index)], env)
    # a stand-in parts catalogue (EPC) for the W210 / W201 and their engines:
    # the Parts tab and its diagrams
    run([py, "tools/make_sample_epc.py", "--out", str(DEV / "epc")], env)
    run([py, "ingest.py", "--archive", str(DEV / "epc"), "--index", str(index)], env)
    # one LEMON vehicle in; the other two are left for the import dialog
    run([py, "ingest.py", "--archive", str(lemon), "--index", str(index),
         "--importer", "lemon", "--only", "2015-ford-f-150"], env)


def open_when_up(url: str) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "api/me", timeout=1)
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Throwaway local AutoRundown with sample data.")
    ap.add_argument("--empty", action="store_true",
                    help="start with an empty index (first-start import dialog); "
                         "the empty index is recreated on every --empty start")
    ap.add_argument("--reset", action="store_true", help="delete .dev/ first")
    ap.add_argument("--port", type=int, default=8757)
    ap.add_argument("--lan", action="store_true",
                    help="also answer on the network (to try it on a phone); this also "
                         "announces autorundown.local, so not while the real one runs")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--fake-api", action="store_true",
                    help="answer Ask from tools/fake_anthropic.py on port+1 (free)")
    ap.add_argument("--fake-vm", action="store_true",
                    help="the Mercedes VM tab runs tools/fake_extractor (free, no VirtualBox)")
    args = ap.parse_args()

    if args.reset and DEV.exists():
        shutil.rmtree(DEV)
    home = DEV / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AUTORUNDOWN_HOME": str(home),
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # never spend by accident from here: no provider's key or address is inherited
    for name in ("ANTHROPIC", "GEMINI", "XAI", "DEEPSEEK"):
        env.pop(f"{name}_API_KEY", None)
        env.pop(f"{name}_BASE_URL", None)
    if args.fake_api:
        sys.path.insert(0, str(ROOT / "tools"))
        import fake_anthropic
        fake = fake_anthropic.serve(args.port + 1)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        # every provider's address is the fake's: that is what keeps a real
        # key typed into Shop setup from spending (providers.py reads these)
        env.update(fake_anthropic.base_urls(args.port + 1))
        env["ANTHROPIC_API_KEY"] = "sk-fake-dev-not-a-real-key"

    if args.empty:
        index = DEV / "empty.db"
        for p in DEV.glob("empty.db*"):
            p.unlink()
        (DEV / "lemon").exists() or run(
            [sys.executable, "tools/make_sample_lemon.py", "--out", str(DEV / "lemon")], env)
    else:
        index = DEV / "index.db"
        if not index.exists():
            build(index, env)
    cfg = home / "config.json"
    conf = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    conf.update(archive_dir=str(DEV / "lemon"), index_path=str(index))
    if args.fake_vm:
        conf["extractor_dir"] = str(ROOT / "tools" / "fake_extractor")
        env["AR_FAKE_VM_OUT"] = str(DEV / "fake_vm_out")
        env["AUTORUNDOWN_PART_PAUSE"] = "5"
    elif conf.get("extractor_dir", "").endswith("fake_extractor"):
        conf["extractor_dir"] = ""        # a --fake-vm run before this one set it
    cfg.write_text(json.dumps(conf, indent=2), encoding="utf-8")

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://localhost:{args.port}/"
    print(f"\nDev AutoRundown: {url}   (data in {DEV}; Ctrl+C stops it)"
          + (f"\nAsk answers from the fake API on {env['ANTHROPIC_BASE_URL']}" if args.fake_api else "")
          + "\n", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_up, args=(url,), daemon=True).start()
    try:
        return subprocess.call([sys.executable, "serve.py", "--index", str(index),
                                "--host", host, "--port", str(args.port)], cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
