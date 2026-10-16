"""Shop setup's Mercedes VM import: WIS / EPC / Both, and its progress -- FREE, ~20 s.

    python tests/test_vm_jobs.py

Nothing here touches VirtualBox or a database: the extractor folder is
tools/fake_extractor, whose vm_pipeline.py prints the real pipeline's kind of
lines and writes a tiny corpus. What runs after it is real -- ingest.py and
remove_manuals.py on a temporary index -- through the real routes:

- the progress parser reads real totals only (images a / b, EPC catalog
  i/N, ingest's vehicles) and leaves a resumed document pull without one;
- "Both" runs WIS then EPC from one POST, as one job: step 1 before step 2,
  EPC's commands only after WIS's import, and EPC ends up in the index;
- a WIS failure stops the job: EPC never starts, the status says which part
  failed and which was not started; an EPC failure leaves WIS done;
- one job at a time; a fresh client (a reloaded page) sees the running
  job's step and progress;
- the old safeguards: an extractor without the EPC pull refuses Both up
  front, and no VirtualBox gives the setup message.
"""
from __future__ import annotations

from _paths import ROOT
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_vmjobs_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")   # before importing autorundown
os.environ["AR_FAKE_VM_OUT"] = str(TMP / "vm_out")
os.environ["AR_FAKE_VM_DELAY"] = "0.03"

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import importer_jobs, server  # noqa: E402
import ingest  # noqa: E402
import test_records  # noqa: E402  (its synthetic WIS corpus)

FAKE = ROOT / "tools" / "fake_extractor"
fails = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global fails
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail != "" and not ok else ""))
    fails += not ok


def feed(lines: list[str]) -> dict:
    p = importer_jobs.Progress()
    for l in lines:
        p.feed(l)
    return p.as_dict()


def parser_checks() -> None:
    print("\n-- progress from the log lines --")
    d = feed(["[10:00:00] == images: 272,181 graphics, 30,000 already pulled; 15,000 per batch, VM off between batches",
              "[10:00:01] $ java.exe -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_images.js",
              "    [10:00:02] progress: row 40000/272181 ok=2000 missing=0 MB=30"])
    check("images: pulled before + this batch, of the total", (d["phase"], d["done"], d["total"]) ==
          ("Images", 32000, 272181), d)
    d = feed(["== images: 100 graphics, 0 already pulled", "batch: +15,000 -> 45,000 / 272,181"])
    check("images: the per-batch line resets the count", (d["done"], d["total"]) == (45000, 272181), d)
    d = feed(["[09:00:00] == documents (resumable; ...)",
              "    [09:00:01] PULL start url=x lang=2 out=y watermark=1005000 (resume)",
              "    [09:00:09] progress: 5000 pulled, 3 no-body, at DokID 1010000"])
    check("a resumed document pull has no total (no fake percentage)",
          d["phase"] == "Documents" and d["done"] == 5000 and d["total"] is None, d)
    d = feed(["== documents", "PULL start url=x (fresh)", "total docs for lang 2 = 272607",
              "progress: 5000 pulled, 3 no-body, at DokID 1"])
    check("a fresh document pull counts against the total it printed", (d["done"], d["total"]) == (5000, 272607), d)
    d = feed(["== EPC parts catalogue (all,engines)", "catalogs to pull: 161",
              "catalog 040 (23/161, series 201): 40 records, 900 parts; images {}; 2 s"])
    check("EPC: catalog i of N", (d["phase"], d["done"], d["total"], d["unit"]) ==
          ("Parts catalogues", 23, 161, "catalogs"), d)
    d = feed(["$ ingest.py --archive x --index y --importer records", "found 3 vehicle(s):",
              "=== A ===", "  done: 5 new pages, 9 chunks", "=== B ==="])
    check("ingest: vehicles done of found", (d["phase"], d["done"], d["total"], d["detail"]) ==
          ("Adding to the index", 1, 3, "B"), d)
    d = feed(["== model applicability", "$ java pull_applic.js", "W_Dok2Reihe rows = 812345"])
    check("a phase with no total stays indeterminate", d["total"] is None and "812,345" in d["detail"], d)
    d = feed(["[10:00:00] STOP: document pull kept stopping; re-run to resume"])
    check("STOP lines become the error", d["error"].startswith("document pull kept stopping"), d)
    d = feed(["== images: 10 graphics, 0 already pulled", "something unexpected 1/2 3"])
    check("an unknown line changes nothing", (d["done"], d["total"]) == (0, 10), d)


def wait(c: TestClient, seen: list, timeout: float = 90) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get("/api/import").json()
        seen.append((s.get("part"), s["progress"]["phase"], s["progress"]["done"],
                     s["progress"]["total"], [p["state"] for p in s["parts"]]))
        if s["state"] != "running":
            return s
        time.sleep(0.05)
    raise SystemExit("job did not finish")


def main() -> int:
    try:
        parser_checks()
        archive = TMP / "archive"
        test_records.build_corpus(archive)
        db = TMP / "index.db"
        with contextlib.redirect_stdout(io.StringIO()):
            check("ingest of the WIS sample", ingest.main(["--archive", str(archive), "--index", str(db),
                                                           "--importer", "records"]) == 0)
        app = server.create_app(str(db), TMP / "auth.db")
        server.jobs.PART_PAUSE = 0.3
        pc = TestClient(app, base_url="http://localhost:8756", client=("127.0.0.1", 50000))

        print("\n-- the fake extractor stands in for the VM --")
        r = pc.post("/api/settings", json={"extractor_dir": str(FAKE)})
        check("the extractor folder setting accepts tools/fake_extractor", r.status_code == 200, r.text)
        vms = pc.get("/api/vms").json()
        check("/api/vms lists the fake VMs", "Mercedes EWA (fake)" in [v["name"] for v in vms["vms"]], vms)
        check("... and writes to the temp out root, not the real one",
              Path(vms["out_root"]) == (TMP / "vm_out").resolve(), vms.get("out_root"))

        print("\n-- Both: WIS then EPC, one job --")
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Mercedes EWA (fake)",
                                         "steps": "docs,applic,vehicles,images,build"})
        check("one POST starts it", r.status_code == 200 and r.json()["state"] == "running", r.text)
        check("two parts, labelled", [p["label"] for p in r.json()["parts"]] == ["WIS", "EPC parts"], r.json())
        r2 = pc.post("/api/import", json={"path": "", "mode": "vm_epc", "vm": "Mercedes EWA (fake)"})
        check("one job at a time (409)", r2.status_code == 409, r2.status_code)
        time.sleep(0.4)
        reload = TestClient(app, base_url="http://localhost:8756", client=("127.0.0.1", 50001))
        s = reload.get("/api/import").json()
        check("a reloaded page sees the running job, its step and its progress",
              s["state"] == "running" and s["part"] == 0 and s["progress"]["phase"], s.get("progress"))
        seen: list = []
        s = wait(pc, seen)
        check("finished", s["state"] == "done" and s["returncode"] == 0, s["log"][-5:])
        check("both parts done", [p["state"] for p in s["parts"]] == ["done", "done"], s["parts"])
        # the whole log (the status carries only its last 120 lines)
        log = (TMP / "home" / "import_job.log").read_text(encoding="utf-8").splitlines()
        idx =lambda pat: next((i for i, l in enumerate(log) if pat in l), -1)  # noqa: E731
        i1, i_ing, i2, i_epc = (idx("== step 1 of 2: WIS"), idx("$ ingest.py"),
                                idx("== step 2 of 2: EPC parts"), idx("--steps epc"))
        check("WIS ran, its import followed, then EPC started", -1 < i1 < i_ing < i2 < i_epc, (i1, i_ing, i2, i_epc))
        check("the pause between parts is in the log", idx("letting the PC settle") > i_ing)
        images = [x for x in seen if x[0] == 0 and x[1] == "Images" and x[3]]
        check("WIS progress was determinate in the images phase", bool(images), seen[:5])
        cats = [x for x in seen if x[0] == 1 and x[1] == "Parts catalogues" and x[3] == 6]
        check("EPC progress counted catalogs of 6 during step 2", bool(cats), [x for x in seen if x[0] == 1][:5])
        indet = [x for x in seen if x[1] == "Model links"]
        check("a phase without a total was shown without one", all(x[3] is None for x in indet), indet[:3])
        have = importer_jobs.manuals(db)
        check("EPC is in the index afterwards", have["epc"]["entries"] > 0, have["epc"])
        check("WIS is still there", have["wis"]["entries"] > 0, have["wis"])

        print("\n-- a second Both replaces EPC (old EPC removed first) --")
        pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Mercedes EWA (fake)", "steps": "build"})
        s = wait(pc, [])
        check("done", s["state"] == "done", s["log"][-5:])
        check("the EPC part removed the old EPC before importing it",
              any("remove_manuals.py" in l and "mercedes-epc" in l for l in s["log"]))

        print("\n-- WIS fails: EPC is never started --")
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Fails in documents (fake)",
                                         "steps": "docs,build"})
        check("started", r.status_code == 200, r.text)
        s = wait(pc, [])
        check("job failed", s["state"] == "failed" and s["returncode"] != 0, s["state"])
        check("step 1 failed, step 2 not started", [p["state"] for p in s["parts"]] == ["failed", "not started"]
              and s["failed_part"] == 0, s["parts"])
        check("no EPC command ran", not any("--steps epc" in l for l in s["log"]), s["log"][-6:])
        check("the log says what was not started", any("not starting EPC parts" in l for l in s["log"]))
        check("the progress carries the pipeline's STOP reason",
              "kept stopping" in s["progress"]["error"], s["progress"])

        print("\n-- EPC fails: WIS stays done --")
        pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Fails in EPC (fake)", "steps": "build"})
        s = wait(pc, [])
        check("step 1 done, step 2 failed", [p["state"] for p in s["parts"]] == ["done", "failed"]
              and s["failed_part"] == 1, s["parts"])

        print("\n-- single sources still work --")
        pc.post("/api/import", json={"path": "", "mode": "vm_epc", "vm": "Mercedes EWA (fake)"})
        s = wait(pc, [])
        check("EPC alone: one unlabelled part, done", s["state"] == "done" and len(s["parts"]) == 1
              and s["parts"][0]["label"] == "", s["parts"])
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Mercedes EWA (fake)",
                                         "steps": "docs,nope"})
        check("an unknown step is refused for Both too", r.status_code == 400, r.status_code)

        print("\n-- safeguards --")
        old = TMP / "old_extractor"
        shutil.copytree(FAKE, old, ignore=shutil.ignore_patterns("*.js", "__pycache__"))
        pc.post("/api/settings", json={"extractor_dir": str(old)})
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Mercedes EWA (fake)",
                                         "steps": "build"})
        check("an extractor older than EPC refuses Both before starting",
              r.status_code == 400 and "older than the parts-catalogue" in r.text, r.text)
        novb = TMP / "no_vbox"
        novb.mkdir()
        (novb / "vm_pipeline.py").write_text(
            "import sys\nprint('[10:00:00] STOP: VBoxManage not found -- install VirtualBox')\nsys.exit(1)\n",
            encoding="utf-8")
        pc.post("/api/settings", json={"extractor_dir": str(novb)})
        vms = pc.get("/api/vms").json()
        check("no VirtualBox: /api/vms says so", "VBoxManage not found" in vms.get("error", ""), vms)
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "x", "steps": "build"})
        check("... and Both is refused", r.status_code == 400, r.status_code)
        phone = TestClient(app, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
        check("a phone cannot start or read an import", phone.post(
            "/api/import", json={"path": "", "mode": "vm_both", "vm": "x"}).status_code == 403
              and phone.get("/api/import").status_code == 403)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
