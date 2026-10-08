# AutoRundown

Turn a local archive of vehicle service manuals into a searchable, AI-queryable
repair reference that runs entirely on your own machine.

Ask *"torque for the front brake caliper anchor plate bolts"* and get the value,
the page it came from, and the diagram — for any vehicle in your archive.

**AutoRundown ships no manual content.** You point it at documents you already
have.

```
SPECIFICATION
  Front Brake Caliper Anchor Plate bolts (2 bolts, discard after removal)
  184 lb.ft (250 Nm)
  Front Disc Brake System — 2015 F-150
  [page 6706]

SOURCES
  p6706  Front Brake Caliper Anchor Plate: Removal
  p6709  Front Disc Brake: Removal
```

---

## Why it refuses to guess

A confident wrong torque value is worse than no answer. Two mechanisms enforce
that:

**Citations are verified, not trusted.** The model is told to cite its sources,
but instruction is not proof. Every page and figure it cites is checked against
what retrieval actually returned. Anything else is dropped, and an answer left
with no surviving citation is withheld rather than shown.

**Vehicle filtering happens before ranking, not after.** A spec from the wrong
year or engine can never outrank the right one, because it is never a candidate
in the first place.

"Not found in this archive" is a correct and useful answer here, and the tool is
built to prefer it over a plausible guess.

---

## Install

### Windows: the installer

Download `AutoRundown-<version>.msi` (from the repo's Releases, or the
`AutoRundown-msi` artifact of the "Windows installer" GitHub Actions run) and
double-click it. It needs no admin rights and no Python. It installs for your
user only, in `%LOCALAPPDATA%\Programs\AutoRundown`, and adds AutoRundown to
the Start menu and desktop.

Starting AutoRundown opens a console window (the server) and your browser.
**Close the window to stop it.** The first time, Windows Firewall asks whether
to allow it on private networks: allow it to use AutoRundown from a phone on
your wifi, or decline to keep it on this PC only. With no manuals indexed yet,
the import dialog opens by itself. Set your API key under Settings.

- **"Windows protected your PC"**: the installer is not code-signed yet. Click
  *More info* -> *Run anyway*.
- **Your data is not in the install folder.** The index, settings and spend
  log are in `%USERPROFILE%\.autorundown`. Upgrading (just run a newer
  `.msi`) or uninstalling (Settings -> Apps) never touches them.
- **Importing from a Mercedes VM** needs the WIS extractor, which is not part
  of the installer. Set `extractor_dir` in `%USERPROFILE%\.autorundown\config.json`
  to its folder.
- The command-line tools are installed too:
  `"%LOCALAPPDATA%\Programs\AutoRundown\python\python.exe" "%LOCALAPPDATA%\Programs\AutoRundown\app\ask.py" --usage`.

To build the installer yourself, see `packaging/windows/build.ps1`.

### macOS and Linux: run from source

There is no installer for these yet. Install Python and run the commands
below. Everything works the same, including the phone access.

### Requirements (running from source)

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com) (yours — billing and
  rate limits are yours)
- A local archive of service manuals you have the right to use

No OCR engine, no GPU, no vector database, no cloud services beyond the LLM call.

> **API credit is billed separately from a Claude Pro/Max subscription.** A
> balance shown against a subscription cannot pay for API calls. Top up at
> [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).
> If your key is valid but unfunded you get a clear message, and keyword search
> keeps working regardless.

---

## Quick start (from source)

```bash
git clone https://github.com/Cquester/AutoRundown.git
cd AutoRundown
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

(On macOS, `python3` comes with the Xcode command-line tools or from
python.org. On Linux, use your distribution's `python3` and `python3-venv`.)

Build the index from your archive directory:

```bash
python ingest.py --archive /path/to/your/manuals --index index.db
```

Indexing is resumable — interrupt it and re-run, and it picks up where it
stopped. `--force` rebuilds from scratch.

Start the server:

```bash
python serve.py
```

It prints a `localhost` URL and a LAN URL. The LAN one works from a phone on the
same wifi — same responsive UI, no app to install, no native build. Add it to
your home screen and it opens like an app.

Set your API key on the Settings page, or:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Keys are stored in `~/.autorundown/config.json` (override the location with
`AUTORUNDOWN_HOME`) and are sent only to Anthropic.

### Command line

```bash
# ask a question
python ask.py --vehicle f-150 "torque for the front caliper anchor plate bolts"

# keyword search — no API call, no key needed, costs nothing
python ask.py --vehicle f-150 --search "caliper anchor plate"

# what have I spent?
python ask.py --usage

# list indexed vehicles
python ask.py --list
```

---

## How it answers

1. Claude rewrites your question into manual terminology
   (*"how much oil does it take"* → `engine oil capacity`).
2. Those queries run against a SQLite FTS5 index, BM25-ranked, scoped to the
   selected vehicle.
3. Results from each query are merged **round-robin**, so a multi-part question
   keeps a share of context for every part rather than letting one sub-question
   crowd out the rest.
4. Claude answers from the retrieved passages only, copying values exactly.
5. Every citation is verified against the retrieved set.

Answers are structured rather than prose:

| Field | Holds |
|---|---|
| `specs` | Values only — a torque, a capacity, a clearance |
| `figures` | Diagrams that *are* the answer: tightening sequences, locations |
| `steps` | Ordered procedure, when you asked how to do something |
| `caveats` | Applicability warnings, or a genuine conflict between pages |

That split matters because the manual answers different questions in different
forms. A torque is a number; a tightening sequence is published only as a
bolt-numbering diagram. Without somewhere to put a figure, a model writes a spec
whose value is *"see Fig 15"* — which then renders in large type as though it
were a torque. Figures are shown inline beside the spec.

Citations name the page rather than just numbering it — `p6706 · Front Brake
Caliper Anchor Plate: Removal`. Titles are resolved locally from the index, so
they cost no tokens and cannot be invented.

### Following the wiring

Two kinds of link are reconstructed in the source pane, both free — no API
call is involved in either.

**The manual's own cross-references.** "Refer to: Interior Lamps for schematic
and connector information" was a real link in the source HTML. Those are
preserved and clickable, plus listed under "Referenced pages".

**Connector IDs.** `C906` appears as bare text, but the archive files its
pinout and its physical location on two separate pages. Those are indexed by
id at ingest, so any mention becomes a chip: click it and you get one card
with the connector end view (pin numbers, wire colours, circuit functions,
pigtail part numbers) above the harness routing diagram showing where it sits
on the vehicle.

Only ids that actually resolve to pages are linked — matching the `C###`
pattern blindly would turn DTC codes and table data into dead links. This is
archive-dependent: the tested Ford manual yields 529 connectors, while a 1987
Mercedes manual predates the format and yields none, in which case no chips
appear and nothing else changes.

### Why keyword search rather than embeddings

Manual text is dense with exact terms — part names, `3.5L`, `coil-on-plug`,
`anchor plate` — which is where BM25 is strong and pure vector search is weak.
Letting Claude choose the search terms recovers the natural-language phrasing
embeddings would have given, without a multi-gigabyte model download or an
embedding pass over every chunk.

If retrieval starts missing on vague questions, that is the point to add vectors
alongside BM25 — not before.

---

## Choosing a model

Pick it from the toolbar beside the question box; it applies to the next
question immediately and saves the choice. Also in Settings and via
`ask.py --model`.

| Model | Price /M in–out | Per question | Eval |
|---|---|---|---|
| `claude-opus-5` | $5 / $25 | ~$0.05 | — |
| `claude-sonnet-5` | $3 / $15 | ~$0.03 | 7/7 |
| `claude-haiku-4-5` | $1 / $5 | ~$0.01 | 7/7 |

Retrieval does the finding, so the model's remaining job is reading passages and
copying values exactly — which Haiku handles well at roughly a quarter of
Sonnet's cost.

One difference the eval does not capture: when the same spec is printed on
several pages in several units, Sonnet reliably folds it into one entry citing
every page, while Haiku tends to split it into two entries for the same
fastener. Both are correct; Haiku is just noisier to read.

Diagnostic questions ("why is X not working") pull far more pages into context
than a spec lookup and cost proportionally more — expect $0.07–$0.10 on Sonnet
versus ~$0.03 for a torque value.

Request parameters adapt per model: Haiku 4.5 predates adaptive thinking and the
effort setting, so those are omitted rather than sent and rejected.

---

## Validation

```bash
python eval.py           # free — no API calls
python eval.py --answers # spends credit, prints an estimate first
python eval.py --answers --case caliper-anchor-torque --model claude-haiku-4-5
```

The default check is free and catches indexing, dedup, ranking, and
variant-filter regressions — most of what actually breaks. The answer check is
opt-in, announces its estimated cost, prints a running total so a Ctrl-C still
tells you what was spent, and can be scoped to a single case and a cheap model.

Run the paid check after changing prompts, retrieval, or models — not routinely.
It has already caught a prompt tweak that fixed one problem while silently
breaking another.

**The shipped cases are archive-specific** — they cite page numbers from one
particular manual set and will not pass against a different archive. Replace
them with known answers from your own: look a spec up by hand, note its page,
add an entry. A handful is enough to catch what matters.

Every paid question is logged with its source (`web` / `cli` / `eval`), model,
tokens, and cost, so spend can be attributed after the fact rather than guessed
at.

---

## Adding a new archive format

Everything downstream reads one normalized shape, so supporting a new archive
layout is a single file. Subclass `Importer`, emit `VehicleRecord` and
`PageRecord`, and register it:

```python
@register
class MyImporter(Importer):
    name = "my-archive"

    @classmethod
    def detect(cls, root): ...      # recognize the layout
    def discover(self, root): ...   # yield VehicleRecord
    def pages(self, vehicle): ...   # yield PageRecord
```

Indexing, search, the query layer, and the UI need no changes.

Two importers ship:

- **`lemon`** — HTML manual archives (one directory per vehicle, `pages/*.html`
  plus `images/`).
- **`folder`** — generic fallback for a plain directory of HTML, text, or PDF,
  inferring what metadata it can from paths. PDF support needs
  `pip install pypdf`.

---

## Design notes

Findings from building this that are worth knowing before you extend it.

**HTML archives need no OCR.** The obvious design for scanned manuals puts OCR
at the center — layout-aware table detection, bounding boxes, long resumable
runs. If your archive is already HTML, none of that applies: spec tables are
real `<table>` elements, so row/column association survives intact and a
torque-angle spec like `Stage 1: 62 lb.in (7 Nm), Stage 2: 50°` is preserved
verbatim. Indexing is minutes, not hours. The OCR stage is deliberately absent;
if you point this at genuine scans, that is the piece to add, and the importer
interface is where it plugs in.

**Archives duplicate themselves.** The tested archive files each article under
several folders — roughly 3.2 copies each. Indexed naively, the top 10 results
were really 3 distinct answers repeated. Pages are deduplicated by content hash;
the first copy is indexed and later copies point at it, so citations always name
a canonical page.

**One manual can contain several engines.** A 3.5L manual may also carry pages
for the 2.7L and 5.0L variants — thousands of them. Answering a torque question
from the wrong engine's page is the exact failure this tool must not have, so
such pages are flagged at import and excluded from search by default. A toggle
reveals them, labelled.

Residual risk: some sibling-engine pages sit in the main tree identified only by
their title. The prompt requires stating exact applicability and flagging any
mismatch, but check the cited page when the spec matters.

**Keep the manual's own cross-references.** Manual text is full of "Refer to:
Interior Lamps for schematic and connector information", where the name was a
link to another page. Strip the `href` and you leave a dead reference the
reader can see but not follow — which is precisely the trail needed when
chasing a connector during diagnosis. Roughly half the pages in the tested
archive carry such links. They are preserved as inline markers, rendered as
clickable cross-references in the source pane plus a "Referenced pages" list,
and stripped before the text reaches the model — showing them would invite
citing a page whose content was never retrieved.

**Diagram paths vary.** Images may be flat (`images/GFD41117.jpeg`) or nested
(`images/IMP49Q313/euro210/551068321.png`). References are kept relative to
`images/` rather than reduced to a filename, or nested ones cannot be resolved.
UI chrome under `icons/` is also served as `<img>` and is excluded. A flat
archive hides this bug completely.

Measured on two vehicle archives (~46k source pages, ~865 MB of HTML and
images): 16,034 unique pages after dedup, 21,401 searchable chunks, 189 MB
index, 39 seconds to build.

---

## Project layout

```
autorundown/
  importers/       archive-specific code lives ONLY here
    base.py          Importer interface + normalized records
    lemon.py         HTML archive importer
    folder.py        generic fallback (HTML / text / PDF)
  extract.py       HTML -> text, tables -> pipe tables
  index.py         SQLite FTS5 schema, chunking, dedup, connectors, usage log
  search.py        BM25 retrieval with mandatory vehicle filter
  query.py         Claude planner + answerer + citation verification
  server.py        local web server
  web/             the UI (one responsive page)
ingest.py          build the index
ask.py             command-line queries
eval.py            known-answer validation
serve.py           start the server
```

---

## Non-goals

No ads. No hosting or redistributing manual content. No native mobile app. No
multi-tenant cloud version.

---

## Safety

AutoRundown's answers are written by an AI model from the manual pages it
retrieves. It checks that every cited page was actually retrieved and refuses
rather than guessing, but the model can still misread a page, pick the wrong
row of a table, or miss a note that applies to your vehicle. **Always open the
cited page and check the value or procedure yourself before you tighten, fill,
wire or replace anything.** Torque, fluid and electrical mistakes can damage a
vehicle and injure people. You use this software at your own risk; see the
warranty disclaimer in [LICENSE](LICENSE).

---

## Legal

**This project is a document parser and search tool. It ships no manual
content, and none is bundled, hosted, or distributed with it.**

Service manuals are generally copyrighted, and public availability is not the
same as a licence. You are responsible for having lawful rights to whatever
archive you point this at.

AutoRundown works equally well on legitimately purchased factory PDFs, on
manufacturer subscription exports you are entitled to, and on manuals that came
with a vehicle — and that is its intended use. Pointing it at material
redistributed without the publisher's permission does not become lawful because
a tool made it searchable; that remains the archive owner's decision and
responsibility, not something this software grants.

**Trademarks.** Mercedes-Benz, WIS, EPC and other names are trademarks of their
owners and are used here only to describe what the software works with.
AutoRundown is an independent project, not affiliated with, endorsed by or
sponsored by Mercedes-Benz Group AG or any other manufacturer. The Mercedes WIS
extractor reads only an installation the user is licensed to use; the user is
responsible for complying with the terms of that licence.

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 cquester.

The licence covers this project's own code and documentation only. It grants
nothing in any service manual, in Mercedes-Benz WIS/EPC software or data, or
in anything extracted from them; none of that is, or may be, committed to this
repository.
