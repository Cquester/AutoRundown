"""Local web server. Binds to the LAN so phones in the shop can reach it.

    python serve.py --index index.db

One responsive UI serves desktop and phone; there is no second codebase and
no native build.
"""
from __future__ import annotations

import argparse
import re
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import config, index, query
from . import importer_jobs, synonyms, wis_original
from .search import get_page, scope_ids, search

jobs = importer_jobs.ImportJobs()

WEB_DIR = Path(__file__).parent / "web"


class AskBody(BaseModel):
    doc_id: str
    question: str
    include_other_variants: bool = False
    # index into the vehicle's variants: adds that model's engine documents
    variant: int | None = None
    # engine family ("272"): that engine's cars and documents only
    engine: str | None = None
    # the car's model year, when the user gave one ("2009 E550 4Matic")
    year: int | None = None
    # per-request override so the model can be switched between questions
    # without saving settings first
    model: str | None = None


class SettingsBody(BaseModel):
    archive_dir: str | None = None
    index_path: str | None = None
    importer: str | None = None
    api_key: str | None = None
    model: str | None = None


class ImportBody(BaseModel):
    path: str
    # add: ingest.py (resumable, keeps what is there)
    # rebuild_wis: rebuild_wis.py (replace the Mercedes WIS part, keep the rest)
    mode: str = "add"
    # mode "vm": extract from this VirtualBox VM first (vm_pipeline.py)
    vm: str = ""
    steps: str = ""


def _vehicle_or_404(conn, doc_id: str) -> dict:
    for v in index.vehicles(conn):
        if v["doc_id"] == doc_id:
            return v
    raise HTTPException(404, f"unknown vehicle {doc_id}")


def create_app(index_path: str) -> FastAPI:
    app = FastAPI(title="AutoRundown")
    app.state.index_path = index_path

    def db():
        # SQLite connections are per-thread; make a fresh one per request.
        return index.connect(app.state.index_path)

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse({
            "name": "AutoRundown",
            "short_name": "AutoRundown",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#12141a",
            "theme_color": "#12141a",
            "icons": [],
        })

    @app.get("/api/stats")
    def stats() -> dict:
        conn = db()
        try:
            cfg = config.load()
            return {
                **index.stats(conn),
                "index_path": app.state.index_path,
                "archive_dir": cfg.archive_dir,
                "has_api_key": bool(cfg.resolved_key),
            }
        finally:
            conn.close()

    @app.get("/api/vehicles")
    def vehicles() -> list[dict]:
        conn = db()
        try:
            return index.vehicles(conn)
        finally:
            conn.close()

    @app.get("/api/search")
    def api_search(doc_id: str, q: str, limit: int = 12,
                   include_other_variants: bool = False,
                   variant: int | None = None, engine: str | None = None) -> list[dict]:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            scope = scope_ids(vehicle, variant, engine)
            # the manual's wording first ("valve cover" -> "cylinder head
            # cover"), then the user's own words
            alt = synonyms.translate(q)
            rows = []
            for query_text in ([alt, q] if alt else [q]):
                for r in search(conn, scope, query_text, limit * 2,
                                include_other_variants=include_other_variants):
                    r["query"] = query_text
                    rows.append(r)
            # one row per page: a page whose two chunks both match is still
            # one result, and the keyword list showed it twice
            seen, out = set(), []
            for r in rows:
                if r["page_no"] not in seen:
                    seen.add(r["page_no"])
                    r.pop("body", None)
                    out.append(r)
            return out[:limit]
        finally:
            conn.close()

    @app.post("/api/ask")
    def api_ask(body: AskBody) -> dict:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, body.doc_id)
            cfg = config.load()
            try:
                chosen = body.model or cfg.model or query.DEFAULT_MODEL
                if chosen not in query.MODELS:
                    raise HTTPException(400, f"unknown model {chosen}")
                result = query.ask(
                    conn, vehicle, body.question, cfg.resolved_key,
                    include_other_variants=body.include_other_variants,
                    model=chosen, source="web", variant=body.variant,
                    engine=body.engine, year=body.year)
            except (query.NoApiKey, query.ApiUnavailable) as exc:
                raise HTTPException(400, str(exc))
            return result.as_dict()
        finally:
            conn.close()

    @app.get("/api/page/{doc_id}/{page_no}")
    def api_page(doc_id: str, page_no: str) -> dict:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            page = get_page(conn, scope_ids(vehicle), page_no)
            if not page:
                raise HTTPException(404, "page not found")
            page["images"] = [i for i in (page.get("images") or "").split("\n") if i]
            # "pageno::label" -> objects the UI can render as links
            page["links"] = [
                {"page": raw.split("::", 1)[0], "label": raw.split("::", 1)[1]}
                for raw in (page.get("links") or "").split("\n")
                if "::" in raw
            ]
            return page
        finally:
            conn.close()

    @app.get("/api/original/{doc_id}/{page_no}", response_class=HTMLResponse)
    def api_original(doc_id: str, page_no: str) -> HTMLResponse:
        """A WIS document redrawn from its SIDS layout (wis_original.py).

        Figures go through /api/image like everywhere else -- every WIS
        vehicle's file_path is the corpus dir, so any WIS doc_id serves them.
        """
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            page = get_page(conn, scope_ids(vehicle), page_no)
        finally:
            conn.close()
        if not page or not page_no.isdigit() or not re.match(
                r"mercedes-wis", vehicle.get("source_name") or ""):
            raise HTTPException(404, "no original layout for this page")
        from urllib.parse import quote
        try:
            body = wis_original.render(
                Path(vehicle["file_path"]), int(page_no), page.get("title") or "",
                lambda f: f"/api/image/{quote(doc_id)}/{quote(f)}")
        except wis_original.NotAvailable as exc:
            return HTMLResponse(f"<p style='font-family:sans-serif'>{exc}</p>", 404)
        return HTMLResponse(body)

    # `:path` because diagrams may sit in nested directories, e.g.
    # images/IMP49Q313/euro210/551068321.png
    @app.get("/api/image/{doc_id}/{name:path}")
    def api_image(doc_id: str, name: str) -> FileResponse:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
        finally:
            conn.close()
        images_dir = (Path(vehicle["file_path"]) / "images").resolve()
        if not images_dir.is_dir():
            # the index survived a move but the archive did not come with it
            raise HTTPException(
                404,
                f"Archive not found at {vehicle['file_path']}. Diagrams are "
                f"served from the extracted archive, not the index. Extract "
                f"it and run: python ingest.py --archive <path> --repath")
        target = (images_dir / name).resolve()
        # never serve outside the vehicle's own images directory
        try:
            target.relative_to(images_dir)
        except ValueError:
            raise HTTPException(404, "image not found")
        if not target.is_file():
            raise HTTPException(404, "image not found")
        return FileResponse(target)

    @app.get("/api/connectors/{doc_id}")
    def api_connector_ids(doc_id: str) -> list[str]:
        """Every connector id known for this vehicle.

        The UI needs the whole set to decide which "C906"-looking tokens in a
        page are real connectors worth linking; matching the pattern blindly
        would turn DTC codes and table data into dead links.
        """
        conn = db()
        try:
            _vehicle_or_404(conn, doc_id)
            return index.connector_ids(conn, doc_id)
        finally:
            conn.close()

    @app.get("/api/connector/{doc_id}/{connector_id}")
    def api_connector(doc_id: str, connector_id: str) -> dict:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            entries = index.connector_pages(conn, doc_id, connector_id)
            if not entries:
                raise HTTPException(404, f"no pages for connector {connector_id}")
            for e in entries:
                page = get_page(conn, scope_ids(vehicle), e["page_no"]) or {}
                e["text"] = page.get("text", "")
                e["images"] = [
                    i for i in (page.get("images") or "").split("\n") if i]
            return {"connector_id": connector_id.upper(), "entries": entries}
        finally:
            conn.close()

    @app.get("/api/models")
    def models() -> list[dict]:
        return [
            {"id": mid, "label": spec["label"],
             "price_in": spec["price_in"], "price_out": spec["price_out"]}
            for mid, spec in query.MODELS.items()
        ]

    # -- import ------------------------------------------------------------
    # Browsing folders and starting an import reach into this PC's files and
    # run a long job, so they answer only to the machine the server runs on --
    # never to a phone on the LAN, which the server otherwise welcomes.
    def _local_only(request: Request) -> None:
        host = (request.client.host if request.client else "") or ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "Importing is only available on the PC running AutoRundown.")

    @app.get("/api/browse")
    def api_browse(request: Request, path: str = "") -> dict:
        _local_only(request)
        p = Path(path).expanduser() if path else Path(config.load().archive_dir or Path.home())
        if not p.is_dir():
            p = Path.home()
        try:
            dirs = sorted((c.name for c in p.iterdir()
                           if c.is_dir() and not c.name.startswith(".")), key=str.lower)
        except OSError:
            dirs = []
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
                "dirs": dirs[:500], "detected": importer_jobs.describe(p)}

    @app.get("/api/vms")
    def api_vms(request: Request) -> dict:
        _local_only(request)
        return importer_jobs.list_vms()

    @app.get("/api/import")
    def api_import_status(request: Request) -> dict:
        _local_only(request)
        return jobs.status()

    @app.post("/api/import")
    def api_import_start(request: Request, body: ImportBody) -> dict:
        _local_only(request)
        if body.mode == "vm":
            info = importer_jobs.list_vms()
            if info.get("error"):
                raise HTTPException(400, str(info["error"]))
            if body.vm not in {v["name"] for v in info["vms"]}:
                raise HTTPException(400, f"No VirtualBox VM named {body.vm!r}.")
            steps = [s for s in body.steps.split(",") if s]
            if any(s not in ("docs", "applic", "vehicles", "images", "build") for s in steps):
                raise HTTPException(400, f"unknown step in {body.steps!r}")
            try:
                return jobs.start(Path(info["out_root"]) / "wis_extract", app.state.index_path,
                                  "vm", vm=body.vm, steps=",".join(steps))
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        p = Path(body.path).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Folder not found: {p}")
        if body.mode not in ("add", "rebuild_wis"):
            raise HTTPException(400, f"unknown mode {body.mode!r}")
        if body.mode == "rebuild_wis" and not importer_jobs.can_rebuild_wis():
            raise HTTPException(400, "No pre-WIS backup (index.before-wis.db) to rebuild from.")
        try:
            return jobs.start(p, app.state.index_path, body.mode)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/settings")
    def get_settings() -> dict:
        cfg = config.load()
        return {
            "archive_dir": cfg.archive_dir,
            "index_path": cfg.index_path,
            "importer": cfg.importer,
            "model": cfg.model,
            "has_api_key": bool(cfg.resolved_key),
            "key_source": ("config" if cfg.api_key
                           else "environment" if cfg.resolved_key else "none"),
        }

    @app.post("/api/settings")
    def post_settings(body: SettingsBody) -> dict:
        cfg = config.load()
        for field_name in ("archive_dir", "index_path", "importer", "api_key",
                           "model"):
            value = getattr(body, field_name)
            if value is not None:
                setattr(cfg, field_name, value)
        cfg.save()
        app.state.index_path = cfg.index_path
        return {"ok": True}

    return app


def lan_ip() -> str:
    """Best-effort LAN address to print for phone access."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    cfg = config.load()
    ap = argparse.ArgumentParser(description="Serve the repair assistant UI.")
    ap.add_argument("--index", default=cfg.index_path)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8756)
    args = ap.parse_args(argv)

    print(f"index: {args.index}")
    print(f"  local : http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        print(f"  LAN   : http://{lan_ip()}:{args.port}   (phones on same wifi)")
    uvicorn.run(create_app(args.index), host=args.host, port=args.port,
                log_level="warning")
    return 0
