# Note on this folder

This is the WIS extractor's source, kept in the repo with its documentation.
The working copy the site runs today is
`../Mercedes Documents/autorundown_extractor`, because the scripts expect to sit
next to `autorundown_out/` (output) and `ewa_extract/` (the EWA install, where
they find the bundled Java and `tbjdbc.jar`). Point the site at either copy with
`extractor_dir` in `~/.autorundown/config.json`; pass `--out-root` and `--java`
/ `--jar` to `vm_pipeline.py` when running from here. Making this folder the
only copy is an open task (CLAUDE.md, "Where we left off").

Start with `docs/MERCEDES_WIS_EXTRACTION.md`.
