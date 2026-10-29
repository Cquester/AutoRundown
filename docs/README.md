# docs

- [`SETUP-ON-NEW-PC.md`](SETUP-ON-NEW-PC.md) — carrying code, index and
  manuals to another PC by OneDrive (the older route; most setups use the MSI
  or `git clone`).
- [`MERCEDES_WIS_EXTRACTION.md`](MERCEDES_WIS_EXTRACTION.md) — how the WIS
  extractor gets data out of a licensed WIS install. The extractor's own docs
  are in [`../extractor/`](../extractor/) (`IMPORT_SPEC.md` is the corpus
  contract).
- [`notes/`](notes/) — the working notes behind each area: what was decided,
  what was measured, and what broke. Read the matching note before changing
  an area (`CLAUDE.md` has the map).
  - [`wis-source.md`](notes/wis-source.md) — WIS extractor, `records` importer, images, reference tables
  - [`vehicle-selection.md`](notes/vehicle-selection.md) — car box, variants, engines, VIN, years
  - [`shop-network.md`](notes/shop-network.md) — admin, mechanic sign-in, spend caps, HTTPS
  - [`importing.md`](notes/importing.md) — Shop setup, import jobs, removing manuals, LEMON
  - [`windows-installer.md`](notes/windows-installer.md) — MSI, launcher, CI, releases
  - [`test-environments.md`](notes/test-environments.md) — test boxes and Windows Sandbox
  - [`ai-providers.md`](notes/ai-providers.md) — the four AI providers, models, prices, what is unverified
  - [`epc-source.md`](notes/epc-source.md) — EPC parts catalogue, the EPC↔WIS join, the right box's tabs (LEMON's too)
- [`qa/`](qa/) — the QA agents' reports from 2026-10-09/10 (`REPORT.md` has
  every finding and its status). File names in them are as they were then
  (before `tests/` and `tools/`).
- [`images/`](images/) — README GIFs, recorded from sample data by
  `python tools/readme_gifs.py`.
