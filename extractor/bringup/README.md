# AutoRundown — WIS/EPC source, status

Source: Mercedes EWA net install recovered from a powered-off VirtualBox VM.
The VM was never booted and the VDI was never modified.

## What is ready now

| Artifact | Location |
|---|---|
| Extracted content, checksum-verified | `..\ewa_extract\` (1,031 files, 40.84 GiB, 0 mismatches) |
| Checksums / manifest | `..\ewa_extract\SHA256SUMS.tsv`, `MANIFEST.tsv` |
| **WIS document catalogue** | `wis_catalog.tsv` — 1,207,456 rows |
| Boot script (needs elevation) | `01_boot_transbase.ps1` |
| Schema dump script | `02_dump_schema.ps1` |

### wis_catalog.tsv

Columns: `language`, `index`, `docnr`, `id`.

Extracted directly from the WIS Lucene 3.6.2 indexes — no database required.
Per language:

    d 125,697 · e 124,699 · s 112,448 · f 105,905 · i 100,955 · tr 91,482
    zh 83,339 · j 81,994 · pg 80,129 · rus 56,255 · pl 55,049 · g 39,666
    ro 32,815 · ko 31,282 · cz 30,902 · swe 17,987 · dk 15,871 · sf 7,764
    us 7,119 · ndl 6,098

`docnr` is the WIS document number in compressed form (12–14 chars):

    ar 0000 p 0100 a   ->  AR00.00-P-0100A
    │  │    │ │    └─ variant (1–3 chars)
    │  │    │ └────── document number (4 digits)
    │  │    └──────── type letter
    │  └───────────── group.subgroup (4 digits)
    └──────────────── group code

English group codes: `ar` 22,952 · `ws` 9,884 · `ap` 2,842 · `sm` 2,828 ·
`pe` 2,274 · `si` 2,257 · `gf` 1,536. Type letters: `p` 16,513 · `w` 6,317 ·
`b` 5,045 · `n` 5,001 · `s` 4,271 · `d` 3,910 · `g` 2,238 · `t` 1,275.

Letter semantics (`p` = procedure, `n` = torque, …) are **inferred from WIS
convention and not yet verified** — confirm against titles once text is readable.

`id` is the internal document id and is the join key to the document text
held in Transbase. Lucene stores only `id` and `docnr`; `title` and `contents`
are indexed but **not stored**, so text cannot be recovered from the index.

## What is blocked, and why

Getting document *text* requires the Transbase engine. Everything is
configured and working except the final boot step:

- Licences: found and accepted (`tblic.ini`, unlimited, OEM ProQuest).
- Paths: all 158 `file=`/`dbpath=` entries repointed and verified to resolve.
- `tbadm32 -i wisnet` reads all 10 WIS cluster files correctly.
- `tbadm32 -b wisnet` fails: `sm_create()` → `os_get_mem(key=0x3)`.

**Cause (verified, not guessed):** Transbase creates its shared memory in the
Windows `Global\` object namespace, which needs `SeCreateGlobalPrivilege`.
Measured on this host:

    Global\<name> file mapping -> ACCESS_DENIED (err 5)
    Local\<name>  file mapping -> OK

So `01_boot_transbase.ps1` must be run **as Administrator**. Nothing else is
known to be missing.

## Connection details (from the shipped install scripts)

    driver   transbase.jdbc.Driver        (tbjdbc.jar, Bundle-Version 3.1)
    url      jdbc:transbase://<host>:<port>/<db>
    user     tbadmin
    password (empty)

Ports: EPC `2034/2035` · ewacore `2044/2045` · WIS `2054/2055`.
Bundled JRE (no javac): `..\ewa_extract\Program Files\EWA\apps\jre\sun_jre\bin\java.exe`.
SQL runner without javac: `..\ewa_extract\Program Files\EWA\install\tools\qml.jar`.

Note: `admin`/`password` is the EWA *web application* login, not the database
credential.

## Engine facts

| | EPC | WIS |
|---|---|---|
| Transbase | V6.6.2.15 (2007) | V6.8.1.83.16 (2015) |
| Page size | 32 KB | 8 KB |
| Codepage | Utf8 | `Propriet` (proprietary) |
| Case insensitive | on | off |
| Encrypted | no | no |
| Clusters | 1–5 per db | 10 |

WIS uses a non-UTF-8 codepage, so expect a character-mapping step there that
EPC will not need.

## Host warning

This machine hard powers off under sustained heavy disk I/O (Kernel-Power 41,
no bugcheck, no WHEA — power delivery or thermal, not software). Three crashes
occurred at ~2.2 GiB/s. All bulk work here is throttled to 150 MB/s, which has
been stable. Keep any bulk job throttled and resumable. One crash silently
corrupted 730 MiB of already-written data that only a verify pass caught.
