# Setting up AutoRundown on the other PC

Everything in this OneDrive folder except your API key, which is deliberately
not here — see the note at the bottom.

## 1. Copy the code somewhere local

Work from a normal folder, not from inside OneDrive:

```
C:\Users\<you>\Desktop\AutoRundown
```

Copy everything from this folder into it **except** `archives/` and this file.

Why not run it from OneDrive: the index is a SQLite database written to
constantly during ingest and on every question. OneDrive syncing a file while
SQLite holds it open is a good way to get a corrupted index, and a git repo
inside a syncing folder produces conflicts of its own.

## 2. Extract the archives

Extract each zip in `archives/` to a local folder — for example
`C:\Users\<you>\Manuals\`. You should end up with:

```
Manuals\
  2015 Ford F-150 Lariat, 4D Pickup Extra Cab, 3.5L Eng VIN G, 4WD\
    index.html
    pages\
    images\
  1987 Mercedes Benz 190E 2.3-16 (201.034) L4-2.3L (102.983)\
```

Extract them **locally, not into OneDrive**. Each one is ~40,000 small files;
syncing that many is slow and gains nothing, which is why only the zips live
here.

## 3. Install

```bash
pip install -r requirements.txt
```

## 3a. Fastest path — use the prebuilt index

`index.db` in this folder is already built for both vehicles. **Copy it into
your local project folder** and you can skip step 2 and the ingest entirely:

```bash
copy "%OneDrive%\AutoRundown\index.db" .
python serve.py
```

Copy it out — do not point `--index` at the OneDrive path. SQLite writes to
the database on every question, and a sync daemon touching an open database
is how you corrupt one.

The index stores the extracted page text, so answers and specs work on their
own. Diagrams do not: images are served from the archive on disk, and the
index records **where that archive was on the machine that built it**. So
after extracting the zips (step 2) you must repoint it once:

```bash
python ingest.py --archive "C:/Users/<you>/Manuals" --index index.db --repath
```

That updates only the archive location — it does not re-index anything, and
takes a second. It prints one line per vehicle, and warns about any vehicle
that is in the index but not found under the path you gave.

Skip this and everything still works except diagrams, which return a message
telling you to run exactly this command.

## 3b. Or rebuild it from the archives

```bash
python ingest.py --archive "C:/Users/<you>/Manuals" --index index.db
```

Takes about 40 seconds for both vehicles. You should see roughly:

```
indexed 45507 pages / 21401 chunks
totals: {'vehicles': 2, 'pages': 45507, 'chunks': 21401,
         'unique_pages': 16034, 'other_variant': 2747}
```

Confirm retrieval is healthy — this costs nothing and makes no API calls:

```bash
python eval.py --index index.db
```

Expect `0 failure(s)`. If pages are missing, the archive path is wrong.

## 4. Add your API key

```bash
python serve.py
```

Open the printed URL, click the gear, paste your key. It is stored in
`~/.autorundown/config.json` on that machine.

## Your API key is NOT in this folder

`config.json` was deliberately left behind. It holds the key in plain text,
and putting that into cloud storage means it syncs to every device on the
account and sits in OneDrive's version history — where it is easy to forget
and hard to fully remove.

Paste the key into Settings on the new machine instead. If you would rather
not have it in a file at all, set `ANTHROPIC_API_KEY` as an environment
variable and leave the Settings field blank; the app reads the environment
when no key is configured.

## What was left behind, and why

| Not copied | Reason |
|---|---|
| Extracted archive folders | ~40,000 files each; the zips are one file |
| `~/.autorundown/config.json` | Contains your API key in plain text |
| `__pycache__/` | Regenerated automatically |

`index.db` **is** included as a convenience copy — copy it out to a local
folder before using it, never open it in place.

## Once your repo exists

Use git for the code from then on, and treat this folder as archive storage
only. `index.db` is already in `.gitignore`, and the manual archives should
never go into the repo — the project ships no manual content, which is the
point of the legal section in the README.

The usage log (what you have spent, by source) lives inside `index.db`, so
rebuilding the index starts that history over. Nothing else depends on it.
