# AutoRundown WIS extractor — one command

Turns a customer's Mercedes **EWA net / WIS** VirtualBox VM into
**`records.jsonl`** for AutoRundown. One script does everything:
snapshot → boot → network → firewall → connect → pull bodies →
pull model applicability → parse.

Each record is stamped with the **models and model-years it applies to**
(`model_series`, `baumuster`, `model_year_from/to`), so AutoRundown can answer
"does this apply to my 1987 190E 2.3-16?" (= Baumuster `201.034`). A `models.jsonl`
catalogue of every model series and its production span is written alongside.

## Run it

```
powershell -ExecutionPolicy Bypass -File extract.ps1 -VM "<vm name>" -GuestUser "<win user>" -GuestPassword "<pw>"
```

- `-VM` is the VirtualBox machine name (`VBoxManage list vms` shows them).
- `-GuestUser` / `-GuestPassword` are the **Windows login inside the VM**. With
  these (and VirtualBox Guest Additions installed in the guest), the firewall step
  is automatic. Without them, the script pauses and asks the user to flip the
  firewall off in the VM themselves.

Output lands in `..\autorundown_out\wis_extract\records.jsonl` by default
(override with `-OutDir`).

## Languages

WIS ships every document in many languages. **List what a given database actually
contains** (SprachCode + ISO code + document count, read from the DB's own tables):

```
powershell -ExecutionPolicy Bypass -File extract.ps1 -VM "<vm name>" -ListLanguages
```

Then extract the one the user wants with `-Lang <SprachCode>`. Codes are the WIS
`SprachCode` (mapped to ISO from the DB's `W_SprachCode2ISOSprachCode` table):

`1 de · 2 en · 3 fr · 4 es · 5 pt · 6 it · 7 nl · 8 da · 9 sv · 10 fi · 12 el ·
13 en-US · 17 tr · 20 ja · 21 hu · 22 ru · 24 cs · 25 pl · 27 bg · 28 zh-CN ·
29 ro · 81 sl · 86 ko` (and any others the DB reports). The extracted records are
stamped with the ISO code in their `lang` field. To build a multi-language corpus,
run the command once per `-Lang` into separate `-OutDir` folders.

### Common options
| flag | meaning | default |
|------|---------|---------|
| `-Lang` | language SprachCode (see Languages above; `-ListLanguages` to discover) | `2` (en) |
| `-ListLanguages` | print the languages in this DB and exit (no extraction) | off |
| `-Database` / `-Port` | `wisnet`/`2054` (WIS) or `SPECIALFILES`/`2034` (EPC) | WIS |
| `-SkipVmSetup` | VM already booted, on host-only, firewall off, app running | off |
| `-NoSnapshot` | don't take the protective snapshot | off |
| `-JavaExe` / `-Jar` | override the auto-detected bundled JRE / `tbjdbc.jar` | auto |
| `-WaitSeconds` | how long to wait for the DB to answer | 240 |

## What's automatic vs. what may need a hand

**Automatic:** finding the tools, snapshotting, booting, switching the NIC to
host-only, discovering the guest DB IP (via Guest Additions or a subnet probe),
the full body pull (resumable), and the parse.

**May need the user**, and the script *tells them exactly what to do and waits*:
1. **Launching the workshop app** — the DB only starts once "Mercedes WSM EPC" is
   running in the guest. If it isn't up, the script prints instructions and waits.
2. **Firewall** — automatic only when guest credentials + Guest Additions are
   present; otherwise the user turns it off in the VM when prompted.

These two exist because they happen *inside* the customer's Windows guest, which
differs per machine. Everything the host controls is fully automated.

## Safe to re-run
The pull checkpoints to `watermark.txt`; if it's interrupted (reboot, timeout),
run the same command again and it resumes. The parse always rebuilds
`records.jsonl` from the local `bodies.pack`, so you can re-parse without
re-pulling.

## Files
| file | role |
|------|------|
| `extract.ps1` | the one command (orchestrator) |
| `langs.js` | lists the languages in the DB (used by `-ListLanguages`) |
| `pull.js` | reads document bodies over JDBC → `bodies.pack` + `metadata.tsv` + `figures.tsv` |
| `pull_applic.js` | reads the model catalogue + doc→model links → `baumusterzeilen.tsv` + `dok2reihe.tsv` (fast, body-free, language-independent) |
| `parse_sids.py` | decoders for all four WIS body formats (SIDS/RTF/XML, gzip+LZW) |
| `parse.py` | raw cache → `records.jsonl` + `models.jsonl` |
| `README.md` | this file |

For the full technical background (schema, the four body formats, the record
shape, pitfalls), see `..\autorundown_out\wis_en\REPRODUCTION.md`.

## Requirements
- Windows host with **VirtualBox** (VBoxManage).
- **Python 3** on PATH (`py` or `python`).
- The customer's licensed EWA/WIS VM, and the bundled JRE + `tbjdbc.jar` (both are
  inside the install; the script auto-finds them under the project folder).
- The user must own their WIS license — this only reads their own installed copy.
