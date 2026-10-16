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
  front, and no VirtualBox gives the setup message;
- the chosen VM is checked first (`/api/vms/check`): a VM without a
  host-only network is not ready and a run on it is refused; an extractor
  too old to check says to update it;
- "Test the VM" (vm_check): Windows not starting gives a failed check and a
  picture of the screen (/api/import/screen), never a power-off; a good VM
  passes both databases;
- Cancel: the job ends `cancelled`, the extractor stops and shuts the VM
  down, nothing after it runs, and the next job is unaffected.
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


def pipeline_checks() -> None:
    """The real extractor's own shutdown, without VirtualBox (its vm_down
    stubbed): found on the real VM on 2026-10-16."""
    print("\n-- the real extractor: shutting down what it started --")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_vmp", ROOT / "extractor" / "vm_pipeline.py")
    vmp = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "extractor"))
    spec.loader.exec_module(vmp)
    done = []

    def fake_down(name):         # a shutdown that ends in a saved state
        done.append(name)
        vmp.STARTED.discard(name)
    vmp.vm_down = fake_down
    vmp.STARTED.update({"A", "B"})
    try:
        vmp.shut_down_started()
        check("every VM it started is shut down, even when one is saved (no crash)",
              sorted(done) == ["A", "B"] and not vmp.STARTED, done)
    except RuntimeError as exc:
        check("every VM it started is shut down, even when one is saved (no crash)", False, exc)
    import re
    src = (ROOT / "extractor" / "vm_pipeline.py").read_text(encoding="utf-8")
    code = "\n".join(l.split("#")[0] for l in src.splitlines() if not l.lstrip().startswith("#"))
    check("the extractor never powers a VM off or discards its saved state",
          not re.search(r'"controlvm"[^)\n]*"poweroff"', code) and '"discardstate"' not in code)


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
        pipeline_checks()
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
        # the WIS part's import is rebuild_wis.py (it replaces WIS in place,
        # or adds it to an index that has none)
        i1, i_ing, i2, i_epc = (idx("== step 1 of 2: WIS"), idx("$ rebuild_wis.py"),
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

        print("\n-- checked before it runs: the VM the user chose --")
        ck = pc.get("/api/vms/check", params={"vm": "Mercedes EWA (fake)", "what": "both"}).json()
        check("a good VM is ready, with a row per need",
              ck["ready"] and {"vm", "network", "java", "jdbc", "scripts", "disk"} <= {c["key"] for c in ck["checks"]},
              ck)
        ck = pc.get("/api/vms/check", params={"vm": "No network (fake)", "what": "wis"}).json()
        check("no host-only network: not ready, and says so",
              not ck["ready"] and any(c["key"] == "network" and c["level"] == "fail" for c in ck["checks"]), ck)
        r = pc.post("/api/import", json={"path": "", "mode": "vm", "vm": "No network (fake)", "steps": "docs"})
        check("... and an extraction on it is refused before anything starts",
              r.status_code == 400 and "host-only" in r.text, r.text)
        check("an unknown VM is refused by the check",
              not pc.get("/api/vms/check", params={"vm": "nope", "what": "wis"}).json()["ready"])

        print("\n-- Test the VM: Windows won't start -> a picture of its screen --")
        r = pc.post("/api/import", json={"path": "", "mode": "vm_check", "vm": "Windows won't start (fake)",
                                         "steps": "docs"})
        check("the VM test starts", r.status_code == 200, r.text)
        s = wait(pc, [])
        check("it fails, and took a picture of the screen", s["state"] == "failed" and s["screen"], s["state"])
        check("its result is in the checks", any(c["key"] == "db_wis" and c["level"] == "fail"
                                                for c in s["checks"]), s["checks"])
        check("... saying Windows did not start", "did not start" in s["progress"]["error"], s["progress"])
        r = pc.get("/api/import/screen")
        check("the picture is served as a PNG", r.status_code == 200 and r.content[:4] == b"\x89PNG",
              r.status_code)
        check("it never powered the VM off", not any("powering off" in l for l in s["log"]))
        r = pc.post("/api/import", json={"path": "", "mode": "vm_check", "vm": "Mercedes EWA (fake)",
                                         "steps": "docs,epc"})
        s = wait(pc, [])
        check("a good VM passes, checking both databases",
              s["state"] == "done" and {"db_wis", "db_epc"} <= {c["key"] for c in s["checks"] if c["level"] == "ok"},
              s["checks"])
        check("... and has no picture", not s["screen"] and pc.get("/api/import/screen").status_code == 404)

        print("\n-- our own snapshot before a run, and rolling back --")
        sn = pc.get("/api/vms/snapshots", params={"vm": "Mercedes EWA (fake)"}).json()
        ours = [x["name"] for x in sn["snapshots"] if x["ours"]]
        check("the runs above took our snapshot (one, reused within 6 h)",
              len([o for o in ours if o.startswith("AutoRundown-auto-")]) == 1, sn)
        r = pc.post("/api/import", json={"path": "", "mode": "vm_rollback", "vm": "Mercedes EWA (fake)",
                                         "snapshot": "Baseline (fake)"})
        check("a rollback starts", r.status_code == 200, r.text)
        s = wait(pc, [])
        check("it keeps the state before the rollback first, then restores",
              s["state"] == "done" and any("SNAPSHOT AutoRundown-before-rollback-" in l for l in s["log"])
              and any("back to Baseline (fake)" in l for l in s["log"]), s["log"][-4:])
        sn = pc.get("/api/vms/snapshots", params={"vm": "Mercedes EWA (fake)"}).json()
        check("... and the VM now runs on the snapshot rolled back to",
              [x["name"] for x in sn["snapshots"] if x["current"]] == ["Baseline (fake)"], sn)
        check("rolling back to a snapshot that isn't there is refused", pc.post("/api/import", json={
            "path": "", "mode": "vm_rollback", "vm": "Mercedes EWA (fake)", "snapshot": "nope"}).status_code == 400)

        # the page asks for a VM's check and its snapshots at once; on a real
        # VirtualBox two VBoxManage calls on one VM collide, so the server
        # runs them one at a time
        import threading
        inside, most = [0], [0]
        real_run = importer_jobs.subprocess.run

        def counting_run(*a, **k):
            inside[0] += 1
            most[0] = max(most[0], inside[0])
            try:
                return real_run(*a, **k)
            finally:
                inside[0] -= 1
        importer_jobs.subprocess.run = counting_run
        try:
            ts = [threading.Thread(target=f) for f in (
                lambda: pc.get("/api/vms/check", params={"vm": "Mercedes EWA (fake)", "what": "wis"}),
                lambda: pc.get("/api/vms/snapshots", params={"vm": "Mercedes EWA (fake)"}),
                lambda: pc.get("/api/vms"))]
            [t.start() for t in ts]
            [t.join() for t in ts]
        finally:
            importer_jobs.subprocess.run = real_run
        check("VirtualBox queries run one at a time (a real VM's lock)", most[0] == 1, most[0])

        print("\n-- Cancel --")
        os.environ["AR_FAKE_VM_DELAY"] = "0.3"           # slow enough to cancel mid-pull
        r = pc.post("/api/import", json={"path": "", "mode": "vm_both", "vm": "Mercedes EWA (fake)",
                                         "steps": "docs,build"})
        time.sleep(2.0)
        r = pc.post("/api/import/cancel")
        check("cancel is accepted while it runs", r.status_code == 200 and r.json()["cancelling"], r.text)
        s = wait(pc, [])
        os.environ["AR_FAKE_VM_DELAY"] = "0.03"
        check("the job ends cancelled, not failed", s["state"] == "cancelled", s["state"])
        check("the extractor stopped and shut the VM down",
              any("STOP: cancelled" in l for l in s["log"]) and any("shutting Mercedes EWA (fake) down" in l
                                                                    for l in s["log"]), s["log"][-6:])
        check("nothing after it ran: no import, EPC not started",
              not any("rebuild_wis.py" in l or "--steps epc" in l for l in s["log"])
              and [p["state"] for p in s["parts"]] == ["cancelled", "not started"], s["parts"])
        check("cancelling with nothing running is a 409", pc.post("/api/import/cancel").status_code == 409)
        check("the next job is not cancelled by the old request",
              pc.post("/api/import", json={"path": "", "mode": "vm_epc", "vm": "Mercedes EWA (fake)"}).status_code == 200
              and wait(pc, [])["state"] == "done")

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
        check("... nor cancel one, check a VM, see its screen or its snapshots",
              phone.post("/api/import/cancel").status_code == 403
              and phone.get("/api/vms/check", params={"vm": "x"}).status_code == 403
              and phone.get("/api/import/screen").status_code == 403
              and phone.get("/api/vms/snapshots", params={"vm": "x"}).status_code == 403)
        bare = TMP / "too_old"
        bare.mkdir()
        (bare / "vm_pipeline.py").write_text(
            # lists its VMs, but knows no --check (argparse would exit 2)
            "import sys\nif '--list-vms' in sys.argv:\n    print('Old VM\\toff'); sys.exit(0)\nsys.exit(2)\n",
            encoding="utf-8")
        pc.post("/api/settings", json={"extractor_dir": str(bare)})
        ck = pc.get("/api/vms/check", params={"vm": "Old VM", "what": "wis"}).json()
        check("an extractor too old to check a VM is not ready, and says to update it",
              not ck["ready"] and "too old" in ck["checks"][0]["message"], ck)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
