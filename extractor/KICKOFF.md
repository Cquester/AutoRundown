# Kickoff prompt — paste this into a new AutoRundown session

Copy everything in the box below into a fresh Claude Code session started in
`<your Mercedes Documents folder>`. Fill in the guest login if you have it.

---

```
You are working on AutoRundown, a local tool that indexes automotive service
manuals and answers repair questions. Your job in this session: run the existing
WIS extractor to pull the English corpus from a Mercedes VM and verify it works
end-to-end (the extracted data was deleted on purpose so this is a clean re-pull).

START HERE:
1. Read the import contract and adapter design:
   <your Mercedes Documents folder>\autorundown_extractor\IMPORT_SPEC.md
   and README.md in the same folder.

WHAT TO RUN (from the autorundown_extractor folder):
- First, list the languages available in the database:
    powershell -ExecutionPolicy Bypass -File extract.ps1 -VM "Mercedes_Ultimate_207" -ListLanguages
- Then extract English:
    powershell -ExecutionPolicy Bypass -File extract.ps1 -VM "Mercedes_Ultimate_207" -Lang 2 -GuestUser "<VM Windows user>" -GuestPassword "<VM Windows password>"
  (If you don't have the VM's Windows login, omit -GuestUser/-GuestPassword. The
   script will pause and tell the user to launch "Mercedes WSM EPC" in the VM and
   turn the guest firewall off, then it continues on its own.)

KNOWN GOOD ENVIRONMENT (already on this machine — do not re-install):
- VM name: Mercedes_Ultimate_207  (confirm with: VBoxManage list vms)
- VirtualBox, Python 3, and ewa_extract\ (holds the bundled Java + tbjdbc.jar the
  script auto-detects) are all present.
- The extractor snapshots the VM before booting, reads the DB over host-only
  networking, and is safe to re-run (it resumes from a checkpoint).

VERIFY SUCCESS after the run — check the output folder
(default: autorundown_out\wis_extract\):
- records.jsonl, models.jsonl and manifest.json all exist.
- manifest.json shows: "schema_version": "1.1", "iso": "en",
  "record_count": 272607, and
  "body_format_counts" approximately { "sids": 232266, "rtf": 40238, "xml": 103 }.
- manifest.json has an "applicability" block with model_lines ~= 33085,
  linked_docs ~= 300000+, models_file "models.jsonl", and records_with_models
  in the hundreds of thousands (NOT 0 — 0 means the applicability pull was skipped).
- Spot-check a few lines of records.jsonl: each has title, body_text,
  body_format, cross_refs, applicability_notes, AND the model fields
  model_series, baumuster, model_year_from, model_year_to, applies_all_series.
- Model/year sanity check (the whole point of schema 1.1): the 1987 190E 2.3-16 is
  Baumuster 201.034 (series 201). Confirm real docs resolve to it, e.g. count
  records whose "baumuster" contains "201.034" (expect ~247 English docs), and that
  a record tagged to series 201 has a model_year range covering 1987. If pre-1990
  models look "missing", check you are filtering on model_series/baumuster/year and
  NOT only on applicability_notes.
Report whether the numbers match. If they differ, investigate before proceeding.

NOTES:
- This is a ONE-TIME offline extraction per database, not a website-runtime step.
  The AutoRundown website only ever reads records.jsonl + models.jsonl + manifest.json.
- The record schema, the four body formats (SIDS/RTF/XML), and the pluggable
  adapter interface are all documented in IMPORT_SPEC.md — follow it.
- To build a multi-language corpus, re-run with a different -Lang into a separate
  -OutDir (see -ListLanguages output for codes).
```

---

That's the whole prompt. The session will read `IMPORT_SPEC.md`, run the extractor,
and confirm the output matches the expected counts.
