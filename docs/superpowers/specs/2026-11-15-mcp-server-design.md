# MCP server for AutoRundown — design (short)

**Intent.** Let Claude (Claude Desktop / Claude Code on *this PC*) look things
up in the user's AutoRundown library: find a car, search its manuals, read
a page, and get its torque specs, fluids, labour times, parts and service
items. Local only for now. Access from outside would be a connector, which
is separate, later work.

**Approach (picked: A).**
- **A, chosen:** `mcp_server.py`, a stdio MCP server that Claude starts. It
  answers each tool by calling the *running* app's free read-only routes on
  `http://127.0.0.1:8756`, so it gets exactly what the UI gets (vehicle
  filter, Validity lines, the car's column, labour ranking).
- B, rejected: open `index.db` directly. That would re-implement route glue
  from `server.py`, which would drift from the UI (wrong-car risk).
- C, deferred: MCP inside the FastAPI app (`/mcp`). This is the base for a
  future connector, and not needed while use is local.

**Goals.** Free, read-only tools; answers Claude can cite by
`doc_id` + `page_no`.
**Non-goals.** No `/api/ask` (no spend on the user's keys), no imports, no
settings, keys, accounts, VM or removal. Not in the MSI yet. No images in
v1.
**Anti-goals.** Never expose an admin action. Never let the MCP server
become a way around the vehicle filter.

**Constraints.**
- The app treats a loopback request as admin, so the MCP server would have
  admin rights. Its tools are therefore an explicit allow-list of GET
  routes, and it sends nothing but GET.
- The address comes from `AUTORUNDOWN_URL`, default
  `http://127.0.0.1:8756`. It must be loopback; anything else is refused.
- If the app is not running, every tool says so plainly ("AutoRundown isn't
  running — start it from the Start menu").

**Tools** (each maps to one existing route):

| tool | route |
|---|---|
| `find_vehicle(query)` | `/api/vehicles`, filtered by words/year/VIN, compact: doc_id, name, years, variants (index, name, engines) |
| `search_manual(doc_id, query, variant?, engine?, tab=workshop\|parts\|torque\|capacity, limit?)` | `/api/search` |
| `read_page(doc_id, page_no)` | `/api/page` (text, links, cross-refs, doc info; no file paths) |
| `manual_contents(doc_id, variant?, engine?, path?)` | `/api/manual/contents` |
| `job_values(doc_id, kind, pages, variant?, engine?)` | `/api/values` |
| `fluids(doc_id, variant?, engine?)` | `/api/fluids` + `/api/wis/oil` |
| `labour_times(doc_id, variant, engine?, query?, book?)` | `/api/wis/labour` |
| `service_schedule(doc_id, variant?, engine?)` / `bulletins(...)` | `/api/wis/service`, `/api/wis/bulletins` |
| `history(limit?)` / `history_entry(id)` | `/api/history` (stored paid answers, shown again for free) |

**Instructions sent to Claude** (the MCP `instructions` field). These carry
over the app's answer rules, because `_verify_citations` does not run here:
answer only from pages the tools returned and cite `doc_id`/`page_no`; give
only the chosen car's values; never convert units; say "not found in the
manual" rather than guess.

**Builder's calls.**
- The official `mcp` Python package (FastMCP), in `requirements-dev.txt`.
- A test, `tests/test_mcp_server.py`: starts the app on sample data at a
  free port, drives every tool over stdio, and checks that no non-GET route
  is reachable. It is added to CI.
- Docs: README section, CLAUDE.md commands, and a config snippet for
  Claude Desktop and Claude Code (`claude mcp add autorundown -- python
  mcp_server.py`).
