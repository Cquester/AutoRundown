"""Local web server. Binds to the LAN so phones in the shop can reach it.

    python serve.py --index index.db
    python serve.py --https          # TLS with a local CA (tls.py)

Who may do what (auth.py): the PC running the server is the admin, with no
sign-in; settings, imports and accounts answer to it alone. Other devices may
search and ask -- freely, or, with `require_signin`, only after signing in
with a mechanic account.

One responsive UI serves desktop and phone; there is no second codebase and
no native build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from typing import Annotated

from pydantic import BaseModel, Field

from . import auth, config, index, query, tls
from . import autostart, importer_jobs, lemon_catalogue, synonyms, wis_links, wis_original
from .search import get_page, parts_slots, scope_ids, search

jobs = importer_jobs.ImportJobs()

WEB_DIR = Path(__file__).parent / "web"


class AskBody(BaseModel):
    doc_id: str
    # every character is paid input; a question is a sentence or two
    question: str = Field(max_length=2000)
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
    extractor_dir: str | None = None
    # Windows: start in the background at sign-in (autostart.py)
    autostart: bool | None = None
    require_signin: bool | None = None


class SigninBody(BaseModel):
    code: str


# dollars a month: -5 used to be stored (the mechanic was refused for good)
# and Infinity / NaN silently meant no limit
Cap = Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)]


class NewUserBody(BaseModel):
    name: str
    monthly_cap_usd: Cap | None = None


class UserUpdateBody(BaseModel):
    disabled: bool | None = None
    monthly_cap_usd: Cap | None = None
    clear_cap: bool = False


class LemonPickBody(BaseModel):
    # a folder of the catalogue tree: [make], [make, year], ... or a variant
    path: list[str]
    picked: bool = True


class ImportBody(BaseModel):
    path: str
    # add: ingest.py (resumable, keeps what is there)
    # rebuild_wis: rebuild_wis.py (replace the Mercedes WIS part, keep the rest)
    mode: str = "add"
    # mode "vm": extract WIS from this VirtualBox VM first (vm_pipeline.py);
    # mode "vm_epc": the EPC parts catalogue from it, then replace EPC in the index
    vm: str = ""
    steps: str = ""
    # mode "lemon": the vehicle folders to import, relative to path
    # mode "remove": the doc_ids to remove (LEMON / folder manuals only)
    vehicles: list[str] = []
    # modes "remove" / "remove_wis" / "remove_epc": VACUUM afterwards to shrink the file
    compact: bool = False


def _vehicle_or_404(conn, doc_id: str) -> dict:
    for v in index.vehicles(conn):
        if v["doc_id"] == doc_id:
            return v
    raise HTTPException(404, f"unknown vehicle {doc_id}")


def _is_wis(vehicle: dict) -> bool:
    return bool(re.match(r"mercedes-wis", vehicle.get("source_name") or ""))


def _corpus_ids(conn, vehicle: dict) -> list[str]:
    """doc_ids of every vehicle built from the same WIS corpus as `vehicle`."""
    return [v["doc_id"] for v in index.vehicles(conn)
            if _is_wis(v) and v.get("file_path") == vehicle.get("file_path")]


def _wis_page(conn, vehicle: dict, page_no: str) -> tuple[dict | None, bool]:
    """A page in the car's scope, else -- WIS only -- the same document filed
    under another model of the same corpus, flagged as outside the scope.

    The second case is a followed cross-reference whose document WIS files
    under other models only. Search and answers never come through here; a
    wrong-model page is shown on request, with a note, never as a result.
    """
    page = get_page(conn, scope_ids(vehicle), page_no)
    if page or not _is_wis(vehicle):
        return page, True
    ids = _corpus_ids(conn, vehicle)
    return (get_page(conn, ids, page_no) if ids else None), False


# answer to any device, signed in or not: the page itself, so it can show
# the sign-in screen, and the CA a phone must install before trusting HTTPS
PUBLIC_PATHS = {"/", "/manifest.webmanifest", "/ca.crt", "/api/me",
                "/api/auth/signin", "/api/auth/signout"}
# answer to the PC running the server only -- they read and change this PC's
# files, settings and API key, and manage accounts
ADMIN_PREFIXES = ("/api/settings", "/api/browse", "/api/vms", "/api/import",
                  "/api/admin/", "/api/shutdown")


# the largest request body accepted; JSON settings, a question or an import
# request are a few KB. Without it a 100 MB body was read into memory first
MAX_BODY = 256 * 1024
# every response: never framed by another site (clickjacking the admin page),
# never content-sniffed, no Referer to anywhere
SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "frame-ancestors 'self'; object-src 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class BodyLimit:
    """Refuse request bodies over MAX_BODY, declared or streamed (ASGI)."""

    class TooLarge(Exception):
        pass

    def __init__(self, app, limit: int = MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > self.limit:
            return await self._refuse(send)
        seen, started = 0, False

        async def counted():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.limit:
                    raise BodyLimit.TooLarge
            return msg

        async def watched(msg):
            nonlocal started
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, counted, watched)
        except BodyLimit.TooLarge:
            if not started:
                await self._refuse(send)

    @staticmethod
    async def _refuse(send):
        body = b'{"detail":"Request too large."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _local_path_error(raw: str) -> str | None:
    """Folders the PC is asked to read must be on the PC: opening
    \\\\server\\share makes Windows connect out and offer this PC's login."""
    if (raw or "").strip().startswith(("\\\\", "//")):
        return "Only folders on this PC can be used, not network shares."
    return None


def _cross_site(request: Request) -> bool:
    """A browser says where a request came from (Sec-Fetch-Site). For the
    admin routes only this page itself (same-origin) or a typed URL (none) will
    do: an <img src="http://localhost:8756/api/vms"> on any web site ran the
    extractor before. Clients that send no such header (curl, the tray's
    --stop) are not browsers and are not affected."""
    site = request.headers.get("sec-fetch-site")
    return site is not None and site not in ("same-origin", "none")


def _is_admin_path(path: str) -> bool:
    return any(path == p or path.startswith(p + ("" if p.endswith("/") else "/"))
               for p in ADMIN_PREFIXES)


def _origin_ok(request: Request) -> bool:
    """A cross-site page must not be able to POST here with our cookie (or as
    the admin, from a browser on this PC). Browsers send Origin on every
    cross-origin POST; it must name the host the request went to."""
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin is None
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].rstrip("/").lower() == host.lower()


# what /api/image will serve: pictures only. An .svg or .html in a manual's
# images/ folder was served as a page of this site, script and all
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff"}
# a diagram opened full size is a document of this origin: no script, ever
IMAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; "
                               "style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


# the iframe's page when there is nothing to draw; the UI looks for the marker
UNAVAILABLE = "<p data-unavailable='1' style='font-family:sans-serif'>{}</p>"


def _index_error(raw: str) -> str | None:
    """Why `raw` can't be the index, or None. Saving "" pointed the server at
    the current folder (every request 500, and it stuck after a restart); a typo
    created a new empty database, so every manual seemed to vanish."""
    if not (raw or "").strip():
        return "The index path can't be empty."
    path = Path(raw.strip()).expanduser()
    if not path.is_file():
        return (f"There is no index file at {path}. Pick an existing index.db "
                "(to build a new one, use Import).")
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("SELECT 1 FROM vehicles LIMIT 1")
        finally:
            con.close()
    except sqlite3.Error:
        return f"{path} is not an AutoRundown index."
    return None


def _extractor_error(raw: str) -> str | None:
    """Why `raw` can't be the extractor folder, or None. Its Python is run
    (/api/vms, imports), so it must be a real local folder holding the
    extractor -- never a network share (\\\\server\\x), which would run
    someone else's code and, on Windows, send this PC's credentials there."""
    raw = (raw or "").strip()
    if not raw:
        return None                      # "" = the repo's own extractor/
    if raw.startswith(("\\\\", "//")):
        return "The extractor folder must be on this PC, not a network share."
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return "Give the extractor folder's full path (e.g. C:\\Users\\you\\…\\extractor)."
    if not (path / "vm_pipeline.py").is_file():
        return f"{path} doesn't hold the Mercedes extractor (vm_pipeline.py)."
    return None


def create_app(index_path: str, auth_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="AutoRundown")
    # vehicles.json is ~750 KB at full size; a phone on slow wifi waited 4.7 s
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    app.add_middleware(BodyLimit)      # added last = runs first
    app.state.index_path = index_path
    app.state.auth_path = auth_path or auth.AUTH_PATH
    app.state.https = False
    app.state.port = 8756
    throttle = auth.Throttle()
    app.state.mdns_name = ""          # serve() sets it once announced
    app.state.shutdown = lambda: None   # make_server() wires the real one

    # tables, indexes and new columns are brought up to date here, before the
    # server answers anything (see index.connect)
    index.connect(index_path).close()

    def db():
        # SQLite connections are per-thread; make a fresh one per request.
        return index.connect(app.state.index_path, setup=False)

    def adb():
        return auth.connect(app.state.auth_path)

    short = socket.gethostname().split(".")[0].lower()
    host_names = {short, f"{short}.local", socket.gethostname().lower(), "autorundown.local"}

    @app.middleware("http")
    async def who_is_asking(request: Request, call_next):
        path = request.url.path
        addr = request.client.host if request.client else ""
        # a name that isn't this PC's means DNS rebinding: a web page whose own
        # name now resolves here, able to search and ask on this PC's key
        names = host_names | ({app.state.mdns_name.lower()} if app.state.mdns_name else set())
        if not auth.host_allowed(request.headers.get("host", ""), names):
            return JSONResponse({"detail": "Unknown host name."}, 421)
        admin = auth.is_admin_request(addr, request.headers.get("host", ""))
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request):
            return JSONResponse({"detail": "Cross-site request refused."}, 403)
        who = auth.Who(admin=True) if admin else None
        if who is None:
            conn = adb()
            try:
                who = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
            finally:
                conn.close()
        request.state.who = who or auth.Who()
        if _is_admin_path(path) and not admin:
            return JSONResponse(
                {"detail": "Only available on the PC running AutoRundown."}, 403)
        if _is_admin_path(path) and _cross_site(request):
            return JSONResponse({"detail": "Cross-site request refused."}, 403)
        if (path not in PUBLIC_PATHS and who is None
                and config.load().require_signin):
            return JSONResponse({"detail": "Sign in to use AutoRundown.",
                                 "signin": True}, 401)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)   # /api/image sets its own CSP
        return response

    @app.get("/api/me")
    def api_me(request: Request) -> dict:
        who: auth.Who = request.state.who
        cfg = config.load()
        out = {
            "admin": who.admin,
            "signed_in": who.user_id is not None,
            "name": who.label,
            "require_signin": cfg.require_signin,
            "default_model": cfg.model or query.DEFAULT_MODEL,
            "https": request.url.scheme == "https",
        }
        if who.user_id is not None:
            conn, aconn = db(), adb()
            try:
                out["month_spend_usd"] = round(index.month_spend(conn, who.user_id), 4)
                out["monthly_cap_usd"] = auth.monthly_cap(aconn, who.user_id)
            finally:
                conn.close()
                aconn.close()
        return out

    @app.post("/api/auth/signin")
    def api_signin(request: Request, body: SigninBody, response: Response) -> dict:
        addr = request.client.host if request.client else ""
        if throttle.blocked(addr):
            raise HTTPException(429, "Too many wrong codes. Wait 15 minutes, or ask for a new code.")
        conn = adb()
        try:
            token = auth.redeem_code(conn, body.code, request.headers.get("user-agent", ""))
        finally:
            conn.close()
        if token is None:
            throttle.fail(addr)
            raise HTTPException(400, "That code is wrong or has expired. Ask for a new one.")
        throttle.ok(addr)
        response.set_cookie(auth.COOKIE, token, max_age=auth.TOKEN_DAYS * 86400,
                            httponly=True, samesite="lax",
                            secure=request.url.scheme == "https")
        return {"ok": True}

    @app.post("/api/auth/signout")
    def api_signout(request: Request, response: Response) -> dict:
        conn = adb()
        try:
            auth.revoke_token(conn, request.cookies.get(auth.COOKIE))
        finally:
            conn.close()
        response.delete_cookie(auth.COOKIE)
        return {"ok": True}

    @app.get("/ca.crt")
    def ca_cert() -> Response:
        if not tls.CA_CRT.exists():
            raise HTTPException(404, "HTTPS is not set up (start with --https).")
        return Response(tls.CA_CRT.read_bytes(), media_type="application/x-x509-ca-cert",
                        headers={"Content-Disposition": 'attachment; filename="autorundown-ca.crt"'})

    def _etagged(request: Request, body: bytes, media_type: str) -> Response:
        """304 when the browser already has this exact body."""
        tag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
        headers = {"ETag": tag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=media_type, headers=headers)

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return _etagged(request, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")

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
    def stats(request: Request) -> dict:
        conn = db()
        try:
            cfg = config.load()
            out = {**index.stats(conn), "has_api_key": bool(cfg.resolved_key)}
            if request.state.who.admin:  # this PC's paths are its own business
                out.update(index_path=app.state.index_path, archive_dir=cfg.archive_dir)
            return out
        finally:
            conn.close()

    @app.get("/api/status")
    def api_status() -> dict:
        """Is an import running? Any device may ask: the page shows "search
        shows partial results until it finishes" while one is. No log, no paths."""
        st = jobs.status()
        return {"importing": st.get("state") == "running"}

    @app.get("/api/vehicles")
    def vehicles(request: Request) -> Response:
        conn = db()
        try:
            # a parts catalogue joined into a manual's car (EPC into WIS) is
            # searched through that car, so it is not listed a second time;
            # lookups by doc_id still find it
            rows = [v for v in index.vehicles(conn) if not v.get("joined_into")]
        finally:
            conn.close()
        if not request.state.who.admin:   # this PC's folders are its own business
            for v in rows:
                v.pop("file_path", None)
        return _etagged(request, json.dumps(rows, separators=(",", ":")).encode(),
                        "application/json")

    @app.get("/api/search")
    def api_search(doc_id: str, q: str = Query(max_length=500),
                   limit: int = Query(12, ge=1, le=50),
                   include_other_variants: bool = False,
                   variant: int | None = None,
                   engine: str | None = Query(None, max_length=64)) -> list[dict]:
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
            seen, out, parts = set(), [], []
            for r in rows:
                if r["page_no"] not in seen:
                    seen.add(r["page_no"])
                    r.pop("body", None)
                    (parts if r.get("parts") else out).append(r)
            # a joined parts catalogue's rows follow the manual's (search.parts_slots)
            return out[:limit] + parts[:parts_slots(limit)]
        finally:
            conn.close()

    @app.post("/api/ask")
    def api_ask(request: Request, body: AskBody) -> dict:
        who: auth.Who = request.state.who
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, body.doc_id)
            cfg = config.load()
            if who.user_id is not None:
                aconn = adb()
                try:
                    cap = auth.monthly_cap(aconn, who.user_id)
                finally:
                    aconn.close()
                spent = index.month_spend(conn, who.user_id)
                if cap is not None and spent >= cap:
                    raise HTTPException(
                        402, f"Monthly limit reached (${spent:.2f} of ${cap:.2f}). "
                             "Keyword search still works; ask the shop to raise the limit.")
            try:
                chosen = body.model or cfg.model or query.DEFAULT_MODEL
                if chosen not in query.MODELS:
                    raise HTTPException(400, f"unknown model {chosen}")
                result = query.ask(
                    conn, vehicle, body.question, cfg.resolved_key,
                    include_other_variants=body.include_other_variants,
                    model=chosen, source="web", variant=body.variant,
                    engine=body.engine, year=body.year,
                    user_id=who.user_id, user_name=who.name or None)
            except (query.NoApiKey, query.ApiUnavailable) as exc:
                raise HTTPException(400, str(exc))
            return result.as_dict()
        finally:
            conn.close()

    @app.get("/api/page/{doc_id}/{page_no}")
    def api_page(request: Request, doc_id: str, page_no: str) -> dict:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            page, in_scope = _wis_page(conn, vehicle, page_no)
            if not page:
                raise HTTPException(404, "page not found")
            if not in_scope:
                owner = next((v for v in index.vehicles(conn)
                              if v["doc_id"] == page["doc_id"]), {})
                page["outside_scope"] = owner.get("model") or page["doc_id"]
            if _is_wis(vehicle):
                # printed document numbers that open a page; the chosen car's
                # variant first (wis_links.py)
                page["xrefs"] = wis_links.resolve(
                    conn, Path(vehicle["file_path"]), page.get("text") or "",
                    scope_ids(vehicle), _corpus_ids(conn, vehicle), page_no)
                page["diagram"] = page_no.isdigit() and wis_links.has_diagram(
                    Path(vehicle["file_path"]), int(page_no))
            if not request.state.who.admin:
                page.pop("file_path", None)
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

    @app.get("/api/wis/oil/{doc_id}")
    def api_wis_oil(doc_id: str, variant: int | None = None,
                    engine: str | None = Query(None, max_length=64)) -> list[dict]:
        """Engine oil capacity for the chosen engine(s) only: a variant's own
        engines, or the engine family picked ("272", "272,276"). With
        neither, nothing -- a series' capacities side by side is the
        wrong-engine mix the picker exists to prevent. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
        finally:
            conn.close()
        if not _is_wis(vehicle):
            return []
        try:
            variants = json.loads(vehicle.get("variants") or "[]")
        except ValueError:
            variants = []
        fams = {f.strip() for f in (engine or "").split(",") if f.strip()}
        if variant is not None and 0 <= variant < len(variants):
            engines = list(variants[variant].get("engines") or [])
        elif fams:
            engines = sorted({e for v in variants for e in v.get("engines") or []})
        else:
            return []
        # the car box sends both: a sales name that changed engine ("E 350",
        # M272 then M276) narrowed by the model year to one family
        if fams:
            engines = [e for e in engines if e.split(".")[0] in fams]
        return wis_links.oil(Path(vehicle["file_path"]), vehicle.get("model_code") or "", engines)

    @app.get("/api/wis/labour/{doc_id}")
    def api_wis_labour(doc_id: str, variant: int | None = None,
                       engine: str | None = Query(None, max_length=64),
                       book: str | None = Query(None, max_length=32),
                       q: str = Query("", max_length=200)) -> dict:
        """Flat-rate labour times from WIS (wis_links.labour_*), for the
        exact model only: a variant must be picked -- a series has as many
        times as it has types. When the car is in several books (chassis
        ranges) and none is chosen, the books come back and no times. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
        finally:
            conn.close()
        if not _is_wis(vehicle):
            raise HTTPException(404, "Labour times come from the Mercedes WIS data.")
        try:
            variants = json.loads(vehicle.get("variants") or "[]")
        except ValueError:
            variants = []
        if variant is None or not 0 <= variant < len(variants):
            return {"books": [], "book": None, "total": 0, "items": [],
                    "need": "variant"}
        v = variants[variant]
        fams = {f.strip() for f in (engine or "").split(",") if f.strip()}
        engines = [e for e in v.get("engines") or [] if not fams or e.split(".")[0] in fams]
        corpus = Path(vehicle["file_path"])
        books = wis_links.labour_books(corpus, list(v.get("baumuster") or []), engines)
        keys = [b["book"] for b in books]
        chosen = book if book in keys else (keys[0] if len(keys) == 1 else None)
        out = {"books": books, "book": chosen, "total": 0, "items": [],
               "need": None if chosen else ("book" if books else None)}
        if chosen:
            out.update(wis_links.labour_times(corpus, chosen, q, alt=synonyms.translate(q) or ""))
        return out

    @app.get("/api/wis/sa/{doc_id}")
    def api_wis_sa(doc_id: str, codes: str = Query(max_length=2000)) -> list[dict]:
        """Option (SA) code descriptions from WIS, for codes off a data card."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
        finally:
            conn.close()
        types = []
        try:
            types = [v.get("vehicle_type") for v in json.loads(vehicle.get("variants") or "[]")]
        except ValueError:
            pass
        # the series' own type: its variants' commonest
        vtype = max(set(t for t in types if t), key=types.count, default="")
        got = (wis_links.sa_codes(Path(vehicle["file_path"]), codes, vtype)
               if _is_wis(vehicle) else None)
        if got is None:
            raise HTTPException(404, "Option codes come from the Mercedes WIS data, "
                                     "which this car's manual is not part of.")
        return got

    @app.get("/api/diagram/{doc_id}/{page_no}")
    def api_diagram(doc_id: str, page_no: str) -> dict:
        """Component codes on a WIS wiring diagram page (wis_links.diagram):
        each code's name, where it is printed on the page's image, and the
        pages its linked document opens, the chosen car's first. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            page, _ = _wis_page(conn, vehicle, page_no)
            if not page or not _is_wis(vehicle) or not page_no.isdigit():
                raise HTTPException(404, "no diagram data for this page")
            images = [i for i in (page.get("images") or "").split("\n") if i]
            corpus = Path(vehicle["file_path"])
            d = wis_links.diagram(corpus, int(page_no), images[0] if images else "")
            if not d:
                raise HTTPException(404, "no diagram data for this page")
            scope, ids = scope_ids(vehicle), _corpus_ids(conn, vehicle)
            for c in d["codes"]:
                c["pages"] = (wis_links.pages_for(conn, corpus, c["docnr"], scope, ids, page_no)
                              if c["docnr"] else [])
            return d
        finally:
            conn.close()

    @app.get("/api/original/{doc_id}/{page_no}", response_class=HTMLResponse)
    def api_original(request: Request, doc_id: str, page_no: str) -> HTMLResponse:
        """A WIS document redrawn from its SIDS layout (wis_original.py).

        Figures go through /api/image like everywhere else -- every WIS
        vehicle's file_path is the corpus dir, so any WIS doc_id serves them.
        """
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            page, _ = _wis_page(conn, vehicle, page_no)
        finally:
            conn.close()
        if not page or not page_no.isdigit() or not re.match(
                r"mercedes-wis", vehicle.get("source_name") or ""):
            return HTMLResponse(UNAVAILABLE.format("There is no original layout for this page."), 404)
        from urllib.parse import quote
        try:
            body = wis_original.render(
                Path(vehicle["file_path"]), int(page_no), page.get("title") or "",
                lambda f: f"/api/image/{quote(doc_id)}/{quote(f)}")
        except wis_original.NotAvailable as exc:
            # the reason names this PC's folders: for the PC only
            why = (str(exc) if request.state.who.admin
                   else "The original layout isn't available for this page.")
            return HTMLResponse(UNAVAILABLE.format(
                why.replace("&", "&amp;").replace("<", "&lt;")), 404)
        return HTMLResponse(body)

    # `:path` because diagrams may sit in nested directories, e.g.
    # images/IMP49Q313/euro210/551068321.png
    @app.get("/api/image/{doc_id}/{name:path}")
    def api_image(request: Request, doc_id: str, name: str) -> FileResponse:
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            # the UI asks through the selected car; a drawing on a joined
            # parts page (EPC with WIS) lives in that catalogue's corpus
            joined = {*vehicle.get("joined_ids", []),
                      *(i for ids in vehicle.get("engine_joins", {}).values() for i in ids)}
            extra = [v["file_path"] for v in index.vehicles(conn)
                     if v["doc_id"] in joined and v.get("file_path")]
        finally:
            conn.close()
        for fp in dict.fromkeys(extra):
            other = (Path(fp) / "images").resolve()
            hit = (other / name).resolve()
            try:
                hit.relative_to(other)      # never outside that images directory
            except ValueError:
                continue
            if hit.is_file() and hit.suffix.lower() in IMAGE_TYPES \
                    and not (Path(vehicle["file_path"]) / "images" / name).is_file():
                return FileResponse(hit, headers=IMAGE_HEADERS)
        images_dir = (Path(vehicle["file_path"]) / "images").resolve()
        if not images_dir.is_dir():
            if not request.state.who.admin:
                raise HTTPException(404, "This manual's diagrams aren't available.")
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
        if not target.is_file() or target.suffix.lower() not in IMAGE_TYPES:
            raise HTTPException(404, "image not found")
        return FileResponse(target, headers=IMAGE_HEADERS)

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
    # run a long job, so they answer only to the machine the server runs on
    # (ADMIN_PREFIXES, enforced in who_is_asking) -- never to a phone on the
    # LAN, which the server otherwise welcomes.

    @app.post("/api/shutdown")
    def api_shutdown() -> dict:
        app.state.shutdown()
        return {"ok": True}

    @app.get("/api/browse")
    def api_browse(path: str = Query("", max_length=1000)) -> dict:
        if _local_path_error(path):
            raise HTTPException(400, _local_path_error(path))
        p = Path(path).expanduser() if path else Path(config.load().archive_dir or Path.home())
        if not p.is_dir():
            if path:
                # a typo used to land in the home folder with Import enabled
                raise HTTPException(404, f"Folder not found: {p}")
            p = Path.home()
        try:
            dirs = sorted((c.name for c in p.iterdir()
                           if c.is_dir() and not c.name.startswith(".")), key=str.lower)
        except OSError:
            dirs = []
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
                "dirs": dirs[:500], "detected": importer_jobs.describe(p, app.state.index_path)}

    @app.get("/api/import/lemon")
    def api_import_lemon(path: str = Query("", max_length=1000)) -> dict:
        if _local_path_error(path):
            raise HTTPException(400, _local_path_error(path))
        p = Path(path).expanduser() if path else Path(config.load().archive_dir or Path.home())
        return importer_jobs.lemon_vehicles(p, app.state.index_path)

    @app.get("/api/import/lemon-catalogue")
    def api_lemon_catalogue(path: str = "[]") -> dict:
        try:
            parts = json.loads(path)
            if not isinstance(parts, list) or len(parts) > 4 or not all(isinstance(x, str) for x in parts):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "path must be a JSON list of up to 4 names")
        try:
            return lemon_catalogue.browse(app.state.index_path, tuple(parts))
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]))

    @app.post("/api/import/lemon-catalogue/picks")
    def api_lemon_pick(body: LemonPickBody) -> dict:
        if not 1 <= len(body.path) <= 4:
            raise HTTPException(400, "path must name a make, year, model or variant")
        try:
            lemon_catalogue.set_pick(tuple(body.path), body.picked)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]))
        return lemon_catalogue.browse(app.state.index_path, tuple(body.path[:-1]))

    @app.get("/api/import/manuals")
    def api_import_manuals() -> dict:
        return importer_jobs.manuals(app.state.index_path)

    @app.get("/api/vms")
    def api_vms() -> dict:
        return importer_jobs.list_vms()

    @app.get("/api/import")
    def api_import_status() -> dict:
        return jobs.status()

    @app.post("/api/import")
    def api_import_start(body: ImportBody) -> dict:
        if body.mode in ("vm", "vm_epc"):
            info = importer_jobs.list_vms()
            if info.get("error"):
                raise HTTPException(400, str(info["error"]))
            if body.vm not in {v["name"] for v in info["vms"]}:
                raise HTTPException(400, f"No VirtualBox VM named {body.vm!r}.")
        if body.mode == "vm_epc":
            # the EPC parts catalogue: vm_pipeline.py --steps epc, then re-ingest
            problem = importer_jobs.epc_problem(importer_jobs.find_extractor())
            if problem:
                raise HTTPException(400, problem)
            try:
                return jobs.start(Path(info["out_root"]) / "epc_extract", app.state.index_path,
                                  "vm_epc", vm=body.vm)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if body.mode == "vm":
            steps = [s for s in body.steps.split(",") if s]
            if any(s not in ("docs", "applic", "vehicles", "images", "build") for s in steps):
                raise HTTPException(400, f"unknown step in {body.steps!r}")
            try:
                return jobs.start(Path(info["out_root"]) / "wis_extract", app.state.index_path,
                                  "vm", vm=body.vm, steps=",".join(steps))
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if body.mode in ("remove", "remove_wis", "remove_epc"):
            have = importer_jobs.manuals(app.state.index_path)
            if body.mode == "remove":
                removable = {r["doc_id"] for g in ("lemon", "folder") for r in have[g]}
                if not body.vehicles:
                    raise HTTPException(400, "Pick at least one manual to remove.")
                unknown = [v for v in body.vehicles if v not in removable]
                if unknown:
                    raise HTTPException(400, f"Not a removable manual: {unknown[0]} "
                                             "(Mercedes WIS and EPC are removed as a whole)")
            elif body.mode == "remove_wis" and not have["wis"]["entries"]:
                raise HTTPException(400, "There is no Mercedes WIS data in the index.")
            elif body.mode == "remove_epc" and not have["epc"]["entries"]:
                raise HTTPException(400, "There is no Mercedes EPC data in the index.")
            try:
                return jobs.start(Path("."), app.state.index_path, body.mode,
                                  only=body.vehicles if body.mode == "remove" else None,
                                  compact=body.compact)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if _local_path_error(body.path):
            raise HTTPException(400, _local_path_error(body.path))
        p = Path(body.path).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Folder not found: {p}")
        if body.mode == "lemon":
            listed = {v["folder"] for v in importer_jobs.lemon_vehicles(
                p, app.state.index_path)["vehicles"]}
            if not body.vehicles:
                raise HTTPException(400, "Pick at least one vehicle.")
            unknown = [v for v in body.vehicles if v not in listed]
            if unknown:
                raise HTTPException(400, f"Not a LEMON vehicle in {p}: {unknown[0]}")
            try:
                return jobs.start(p, app.state.index_path, "lemon", only=body.vehicles)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if body.mode not in ("add", "rebuild_wis"):
            raise HTTPException(400, f"unknown mode {body.mode!r}")
        if body.mode == "rebuild_wis" and not importer_jobs.can_rebuild_wis(app.state.index_path):
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
            "index_path": app.state.index_path,  # the one in use, not the saved one
            "importer": cfg.importer,
            "model": cfg.model,
            "extractor_dir": cfg.extractor_dir,
            "has_api_key": bool(cfg.resolved_key),
            "key_source": ("config" if cfg.api_key
                           else "environment" if cfg.resolved_key else "none"),
            # None: not offered on this OS
            "autostart": autostart.is_enabled() if autostart.supported() else None,
            "phone_urls": _phone_urls(),
            "require_signin": cfg.require_signin,
            "config_path": str(config.CONFIG_PATH),
        }

    @app.post("/api/settings")
    def post_settings(body: SettingsBody) -> dict:
        # check everything before changing anything
        if body.index_path is not None and body.index_path != app.state.index_path:
            err = _index_error(body.index_path)
            if err:
                raise HTTPException(400, err)
            body.index_path = str(Path(body.index_path.strip()).expanduser())
        if body.extractor_dir is not None:
            err = _extractor_error(body.extractor_dir)
            if err:
                raise HTTPException(400, err)
            body.extractor_dir = body.extractor_dir.strip()
        if body.model is not None and body.model not in query.MODELS:
            raise HTTPException(400, f"Unknown model {body.model!r}.")
        if body.autostart is not None and body.autostart != autostart.is_enabled():
            try:
                autostart.set_enabled(body.autostart)
            except RuntimeError as exc:
                raise HTTPException(400, str(exc))
        cfg = config.load()
        for field_name in ("archive_dir", "index_path", "importer", "api_key",
                           "model", "extractor_dir", "require_signin"):
            value = getattr(body, field_name)
            if value is not None:
                setattr(cfg, field_name, value)
        cfg.save()
        # only a new index path moves the server: saving anything else (the
        # model, sign-in) used to swap a `serve.py --index X` server onto the
        # config's index
        if body.index_path is not None:
            index.connect(cfg.index_path).close()   # bring it up to date first
            app.state.index_path = cfg.index_path
        return {"ok": True}

    # -- mechanics' accounts (admin only, see ADMIN_PREFIXES) ---------------
    @app.get("/api/admin/users")
    def admin_users() -> dict:
        conn, aconn = db(), adb()
        try:
            spend = index.month_spend_by_user(conn)
            users = auth.users(aconn)
        finally:
            conn.close()
            aconn.close()
        for u in users:
            q, c = spend.get(u["id"], (0, 0.0))
            u.update(month_questions=q, month_spend_usd=round(c, 4))
        q, c = spend.get(None, (0, 0.0))
        return {"users": users, "unattributed": {"month_questions": q,
                                                 "month_spend_usd": round(c, 4)},
                "require_signin": config.load().require_signin,
                "phone_urls": _phone_urls()}

    @app.post("/api/admin/users")
    def admin_add_user(body: NewUserBody) -> dict:
        aconn = adb()
        try:
            uid = auth.add_user(aconn, body.name, body.monthly_cap_usd)
            return {"id": uid, "code": auth.new_code(aconn, uid),
                    "minutes": auth.CODE_MINUTES}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            aconn.close()

    @app.post("/api/admin/users/{user_id}")
    def admin_update_user(user_id: int, body: UserUpdateBody) -> dict:
        aconn = adb()
        try:
            auth.update_user(aconn, user_id, disabled=body.disabled,
                             cap=body.monthly_cap_usd, clear_cap=body.clear_cap)
        except KeyError:
            raise HTTPException(404, "No such user.")
        finally:
            aconn.close()
        return {"ok": True}

    @app.post("/api/admin/users/{user_id}/code")
    def admin_new_code(user_id: int) -> dict:
        aconn = adb()
        try:
            return {"code": auth.new_code(aconn, user_id), "minutes": auth.CODE_MINUTES}
        except KeyError:
            raise HTTPException(404, "No such user.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            aconn.close()

    @app.post("/api/admin/users/{user_id}/signout")
    def admin_sign_out(user_id: int) -> dict:
        aconn = adb()
        try:
            auth.sign_out_all(aconn, user_id)
        finally:
            aconn.close()
        return {"ok": True}

    def _phone_urls() -> list[str]:
        """autorundown.local first (mdns.py, when announced), then the IPs."""
        scheme = "https" if app.state.https else "http"
        _, ips = tls.local_names()
        hosts = [app.state.mdns_name] if app.state.mdns_name else []
        hosts += [ip for ip in ips if ":" not in ip and not ip.startswith("127.")]
        return [f"{scheme}://{h}:{app.state.port}" for h in hosts]

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


def make_server(index_path: str, host: str = "0.0.0.0", port: int = 8756,
                https: bool = False):
    """The uvicorn server, stoppable from outside (tray, /api/shutdown)."""
    import uvicorn

    app = create_app(index_path)
    app.state.port = port
    ssl = {}
    if https:
        crt, key, names, ips = tls.ensure_server_cert()
        ssl = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
        app.state.https = True
        app.state.cert_names = names + ips
    # proxy_headers off: the client address decides who is admin, and must be
    # the socket's own peer, never an X-Forwarded-For header
    srv = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning",
                                        proxy_headers=False, **ssl))
    app.state.shutdown = lambda: setattr(srv, "should_exit", True)
    return srv


def serve(srv) -> int:
    """Run a make_server() server, announced on the LAN as autorundown.local."""
    from . import mdns

    app, host, port = srv.config.app, srv.config.host, srv.config.port
    scheme = "https" if app.state.https else "http"
    print(f"index: {app.state.index_path}")
    print(f"  admin : {scheme}://localhost:{port}   (this PC: settings, imports, accounts)")
    ann = None
    if host == "0.0.0.0":
        ip = lan_ip()
        ann = mdns.Announcer(port, ip).start()
        if ann.url:
            app.state.mdns_name = f"{ann.name}.local"
        print(f"  LAN   : {scheme}://{ip}:{port}   (phones on same wifi)")
        print(f"  name  : {scheme}://{app.state.mdns_name}:{port}" if ann.url
              else f"  name  : not announced ({ann.error})")
    if app.state.https:
        print(f"  cert  : {', '.join(app.state.cert_names)}")
        print(f"  phones: install {scheme}://{lan_ip()}:{port}/ca.crt once "
              "to remove the warning")
    print(f"  sign-in for other devices: "
          f"{'REQUIRED' if config.load().require_signin else 'off (anyone on the network)'}")
    try:
        srv.run()
    finally:
        if ann:
            ann.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()
    ap = argparse.ArgumentParser(description="Serve the repair assistant UI.")
    ap.add_argument("--index", default=cfg.index_path)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--https", action="store_true",
                    help="serve HTTPS with a certificate from a local CA (tls.py); "
                         "phones install /ca.crt once to trust it")
    args = ap.parse_args(argv)
    return serve(make_server(args.index, args.host, args.port, https=args.https))
