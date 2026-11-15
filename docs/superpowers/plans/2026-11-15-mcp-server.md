# MCP server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A read-only stdio MCP server that lets Claude on this PC look things up in the running AutoRundown app.

**Architecture:** `mcp_server.py` at the repo root. A small `AppClient` sends GET requests to the app's free routes on loopback. The tool functions are plain functions over that client. `MCPServer` (mcp 2.x) registers them and runs on stdio.

**Tech Stack:** Python 3.12+, `mcp>=2.3,<3` (`mcp.server.mcpserver.MCPServer`), stdlib `urllib`.

**Spec:** `docs/superpowers/specs/2026-11-15-mcp-server-design.md`

## Global Constraints
- GET only, to an allow-list of route prefixes. Never `/api/ask` or any admin or import route.
- The address is `AUTORUNDOWN_URL`, default `http://127.0.0.1:8756`. A host that isn't loopback (127.0.0.1, localhost, ::1) is refused at start.
- The app not running → each tool returns "AutoRundown isn't running at <url> — start it from the Start menu."
- No file paths in results (`file_path` dropped).
- `mcp` goes in `requirements-dev.txt` only (not shipped in the MSI).

## Review Focus
- An unknown `doc_id` or `page_no`: the app's 404 detail is passed to Claude, with no traceback.
- A huge `/api/vehicles` (hundreds of rows, thousands of variants): `find_vehicle` returns at most 20 cars, with variants trimmed.
- A VIN typed into `find_vehicle`: matched against the variants' `vin9` prefixes / FIN, or told to search by model.
- `labour_times` with no variant: the app returns `books`/`need_variant`, which is passed through as-is.
- Non-ASCII in queries (German labels, "°"): URL-encoded correctly.

### Task 1: Client, tools, server, test
**Files:** create `mcp_server.py` and `tests/test_mcp_server.py`; modify `requirements-dev.txt`.
- `AppClient(base_url)` with `.get(path, **params) -> Any`. Raises `AppError(str)` on a connection failure or HTTP error (with the detail).
- Tools: `find_vehicle`, `search_manual`, `read_page`, `manual_contents`, `job_values`, `fluids`, `labour_times`, `service_schedule`, `bulletins`, `history`, `history_entry`.
- Test part 1: a recording fake HTTP server drives every tool. Assert that every request was a GET to an allowed path, plus the not-running message and the refusal of a non-loopback address.
- Test part 2: the real `serve.py` on a sample WIS index at a free port, and an MCP stdio client spawning `mcp_server.py`. Assert: `list_tools` gives 11 tools; `find_vehicle` finds a car; `search_manual` gives hits; `read_page` on the first hit gives text; no `file_path` appears anywhere.

### Task 2: CI, docs
- Add `tests/test_mcp_server.py` to ci.yml's "Test suites" loop.
- README: a section "Use it from Claude (MCP)", with the Claude Code and Claude Desktop config.
- CLAUDE.md: the command lines, the repo layout entry, and a decision line.
