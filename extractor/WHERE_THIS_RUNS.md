# Note on this folder

This is the WIS extractor, and the only copy the site runs (since 2026-10-08;
before that it ran `../Mercedes Documents/autorundown_extractor`, which can be
removed). `extractor_dir` in `~/.autorundown/config.json` still overrides it.

**Code here, data outside.** The scripts read the EWA install (the bundled
Java and `tbjdbc.jar`) and write `autorundown_out/` (pulls, the corpus, ~11 GB
of images) in the "Mercedes Documents" folder, never in the repo:
`wis_paths.py` finds it (`AR_OUT_ROOT`, else `AR_HOME`, else the nearest
`Mercedes Documents` beside the repo or a parent of it) and refuses an output
folder inside the repo. `python extractor/wis_paths.py` prints both.
`vm_pipeline.py --out-root` / `--java` / `--jar` override per run.

Not here: the old `pull_images_*.sh` batch scripts (hard-coded paths and IP;
`vm_pipeline.py --steps images` does the same batching) and
`parse_sids.before-lzw.py` (a backup).

Start with `docs/MERCEDES_WIS_EXTRACTION.md`.
