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
import secrets
import socket
import sqlite3
import time
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from typing import Annotated

from pydantic import BaseModel, Field

from . import search as search_mod
from . import auth, config, handoff, history, index, numbers, providers, qr, query, tls
from . import (autostart, html_original, importer_jobs, lemon_catalogue, manual_browse, part_procedures,
               parts_browse, synonyms, tab_values, wis_links, wis_original)
from .search import (as_ids, get_page, part_lines, scope_ids, search, split_scope, value_lines,
                     value_row)

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
    # without saving settings first: any model in the requester's menu
    # (config.menu_models), whichever provider it is from
    model: str | None = None
    # a follow-up in a chat: the history entry the chat is kept in. Its car
    # and earlier turns come from that entry, never from this body.
    chat: int | None = None


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
    # WIS damage-code menus in search and the Manual contents
    show_damage_codes: bool | None = None
    # "company" | "per_mechanic" (config.pick_key)
    key_mode: str | None = Field(None, pattern="^(company|per_mechanic)$")
    # the AI provider answering questions (providers.py)
    provider: str | None = Field(None, pattern="^(anthropic|gemini|xai|deepseek)$")
    # whose company key `api_key` is; default the active provider
    key_provider: str | None = Field(None, pattern="^(anthropic|gemini|xai|deepseek)$")
    # models left out of the question box's menu (config.hidden_models)
    hidden_models: list[str] | None = Field(None, max_length=50)
    # a provider's newer models added from its model list (config.extra_models)
    extra_models: dict[str, list[str]] | None = None


class HandoffBody(BaseModel):
    """A car picked on a phone, sent to the desk (handoff.py): the pick
    itself, checked against the index -- never a label."""
    doc_id: str = Field(max_length=200)
    variant: int | None = Field(None, ge=0, le=100000)
    engine: str | None = Field(None, max_length=40)
    year: int | None = Field(None, ge=1900, le=2100)
    vin: str | None = Field(None, max_length=40)


class SigninBody(BaseModel):
    code: str = Field(max_length=40)


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


class MechanicKeyBody(BaseModel):
    """A mechanic's own API key ("A key per mechanic"). A new key, or
    `reset` ("topped up / new key"), starts the spend count again from now;
    `loaded_usd` is the credit the owner says is on the key at that moment."""
    api_key: str | None = Field(None, max_length=300)
    loaded_usd: Cap | None = None
    clear_loaded: bool = False
    reset: bool = False
    remove: bool = False


class VmPickBody(BaseModel):
    file: str = Field(max_length=1000)    # the VM's .vbox file


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
    # mode "vm_both": "vm" then "vm_epc" as one job; EPC only if WIS succeeded
    vm: str = ""
    steps: str = ""
    # mode "vm_rollback": the snapshot to roll the VM back to
    snapshot: str = Field("", max_length=200)
    # mode "lemon": the vehicle folders to import, relative to path
    # mode "remove": the doc_ids to remove (LEMON / folder manuals only)
    vehicles: list[str] = []


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
# and /signin, where a sign-in QR's link lands
PUBLIC_PATHS = {"/", "/manifest.webmanifest", "/ca.crt", "/api/me",
                "/api/auth/signin", "/api/auth/signout", "/signin"}
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
# what /vendor/ serves (web/vendor: the Scan VIN libraries); licence texts too
VENDOR_TYPES = {".js": "text/javascript; charset=utf-8", ".wasm": "application/wasm",
                ".gz": "application/octet-stream", ".txt": "text/plain; charset=utf-8",
                ".md": "text/plain; charset=utf-8"}
# a diagram opened full size is a document of this origin: no script, ever
IMAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; "
                               "style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


# a LEMON / folder page shown as its own HTML (html_original.py): cleaned to
# an allow-list, and this CSP besides -- only its own script (height, links;
# a nonce per response) runs, pictures and styles from this site only
HTML_ORIGINAL_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
                     "script-src 'nonce-{}'; frame-ancestors 'self'; base-uri 'none'; "
                     "form-action 'none'")


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


_VIN2FIN_CACHE: dict[str, tuple[float, dict]] = {}


def _vin_prefixes(corpus: str) -> dict[str, list[str]]:
    """The corpus's vin2fin.json as Baumuster -> [VIN prefixes], read-only and
    cached until the file changes. {} when it is missing or unreadable."""
    from .importers.records import vin_prefixes_by_bm
    path = Path(corpus)
    try:
        stamp = max((path / n).stat().st_mtime for n in ("vin2fin.json", "manifest.json")
                    if (path / n).is_file())
    except (OSError, ValueError):     # ValueError: no file at all
        return {}
    hit = _VIN2FIN_CACHE.get(corpus)
    if hit and hit[0] == stamp:
        return hit[1]
    data = vin_prefixes_by_bm(path)
    _VIN2FIN_CACHE[corpus] = (stamp, data)
    return data


def _with_vin9_bm(rows: list[dict]) -> list[dict]:
    """Give every variant with US VIN prefixes the exact prefix -> Baumuster map.

    An index built before 2026-10-16 stored only a flat `vin9` list per
    variant, and the page sent the variant's first Baumuster's engine for all
    of them (WDBJF65J0 = 210.065, M112, searched the M104 of 210.055). The
    importer now stores `vin9_bm`; for an older index it is rebuilt here from
    the corpus's own vin2fin.json (`file_path`), so no rebuild is needed.
    Without that file the page falls back to the VIN's year, then to every
    engine of the variant."""
    from .importers.records import add_vin9_bm
    out = []
    for v in rows:
        raw = v.get("variants")
        if raw and '"vin9"' in raw and '"vin9_bm"' not in raw and v.get("file_path"):
            by_bm = _vin_prefixes(v["file_path"])
            if by_bm:
                try:
                    v = {**v, "variants": json.dumps(add_vin9_bm(json.loads(raw), by_bm),
                                                     ensure_ascii=False, separators=(",", ":"))}
                except ValueError:
                    pass
        out.append(v)
    return out


def create_app(index_path: str, auth_path: str | Path | None = None) -> FastAPI:
    # no /docs, /redoc or /openapi.json: every device on the LAN could read
    # the whole API map, and /docs pulled Swagger's script from a CDN onto
    # this app's origin (QA, 2026-10-15)
    app = FastAPI(title="AutoRundown", docs_url=None, redoc_url=None, openapi_url=None)
    # vehicles.json is ~750 KB at full size; a phone on slow wifi waited 4.7 s
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    app.add_middleware(BodyLimit)      # added last = runs first
    app.state.index_path = index_path
    app.state.auth_path = auth_path or auth.AUTH_PATH
    app.state.https = False
    app.state.port = 8756
    # damage-code menus in search and the Manual contents (Shop setup)
    search_mod.show_damage_codes = config.load().show_damage_codes
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
            "default_model": config.active_model(cfg),
            "provider": config.active_provider(cfg),
            "https": request.url.scheme == "https",
        }
        out["key_mode"] = cfg.key_mode
        if who.user_id is not None:
            conn, aconn = db(), adb()
            try:
                out["month_spend_usd"] = round(index.month_spend(conn, who.user_id), 4)
                out["month_questions"] = index.month_questions(conn, who.user_id)
                out["monthly_cap_usd"] = auth.monthly_cap(aconn, who.user_id)
                if cfg.key_mode == "per_mechanic":
                    # their own key's estimate -- never the key, nor its last 4
                    k = _key_status(conn, cfg, who.user_id)
                    out["own_key"] = {f: k[f] for f in
                                      ("has_key", "loaded_usd", "spent_usd", "remaining_usd")}
            finally:
                conn.close()
                aconn.close()
        return out

    def _set_token_cookie(request: Request, response: Response, token: str) -> None:
        # a phone that was signed in as someone else is now this mechanic;
        # its old token is ended, not left behind
        old = request.cookies.get(auth.COOKIE)
        if old:
            conn = adb()
            try:
                auth.revoke_token(conn, old)
            finally:
                conn.close()
        response.set_cookie(auth.COOKIE, token, max_age=auth.TOKEN_DAYS * 86400,
                            httponly=True, samesite="lax",
                            secure=request.url.scheme == "https")

    @app.post("/api/auth/signin")
    def api_signin(request: Request, body: SigninBody, response: Response) -> dict:
        """The typed code under a sign-in QR (for a phone whose camera can't
        scan it): same grant, same single use and 15 minutes."""
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
            raise HTTPException(400, "That code is wrong, used or has expired. "
                                     "Ask for a new sign-in QR.")
        throttle.ok(addr)
        _set_token_cookie(request, response, token)
        return {"ok": True}

    @app.get("/signin")
    def signin_link(request: Request, t: str = Query("", max_length=200)) -> Response:
        """Where a sign-in QR lands: the phone's camera opened
        /signin?t=<token>. The token is used up here (hash looked up, expiry
        and single use checked), the session cookie set, and the phone sent
        to / -- so the token leaves the address bar and its history entry.
        A failure goes to /#signin=<why>, where the page says so."""
        addr = request.client.host if request.client else ""
        no_store = {"Cache-Control": "no-store"}
        # a camera app or a typed address is Sec-Fetch-Site "none"; a link
        # or a hidden frame on another site must not sign this phone in
        if _cross_site(request):
            return RedirectResponse("/#signin=refused", 303, headers=no_store)
        if throttle.blocked(addr):
            return RedirectResponse("/#signin=wait", 303, headers=no_store)
        conn = adb()
        try:
            token = auth.redeem_link(conn, t, request.headers.get("user-agent", ""))
        finally:
            conn.close()
        if token is None:
            throttle.fail(addr)
            return RedirectResponse("/#signin=expired", 303, headers=no_store)
        throttle.ok(addr)
        response = RedirectResponse("/", 303, headers=no_store)
        _set_token_cookie(request, response, token)
        return response

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

    # Scan VIN's libraries (barcode + OCR), vendored with a fixed version in
    # the folder name and loaded by the page only when someone scans: no CDN,
    # so it works on a shop LAN with no internet. Same sign-in rules as every
    # other non-public path. Read-only; nothing is uploaded -- the photo is
    # read on the phone itself.
    @app.get("/vendor/{name:path}")
    def vendor(name: str) -> Response:
        root = (WEB_DIR / "vendor").resolve()
        target = (root / name).resolve()
        kind = VENDOR_TYPES.get(target.suffix.lower())
        if kind is None or not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(target, media_type=kind, headers={
            # the version is in the path: a new version is a new URL
            "Cache-Control": "private, max-age=31536000, immutable"})

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
            who: auth.Who = request.state.who
            try:   # a key this requester may use (theirs, in per-mechanic mode)
                has_key = bool(config.pick_key(cfg, who.user_id, who.admin).key)
            except config.NoKeyForRequest:
                has_key = False
            out = {**index.stats(conn), "has_api_key": has_key}
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
        # a VM test or rollback imports nothing: search is not partial then
        return {"importing": st.get("state") == "running"
                             and st.get("mode") not in ("vm_check", "vm_rollback")}

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
        rows = _with_vin9_bm(rows)
        if not request.state.who.admin:   # this PC's folders are its own business
            for v in rows:
                v.pop("file_path", None)
        return _etagged(request, json.dumps(rows, separators=(",", ":")).encode(),
                        "application/json")

    @app.get("/api/search")
    def api_search(request: Request, doc_id: str, q: str = Query(max_length=500),
                   limit: int = Query(12, ge=1, le=50),
                   include_other_variants: bool = False,
                   variant: int | None = None,
                   engine: str | None = Query(None, max_length=64),
                   tab: str = Query("workshop", pattern="^(workshop|parts|torque|capacity)$"),
                   also: list[str] = Query(default=[]),
                   record: bool = False) -> list[dict]:
        """Keyword search for one of the UI's tabs, always within the car.

        workshop: the manual's pages only (as before the parts catalogue);
        parts: the joined parts catalogue only, each row with the page's
        matching part lines; torque / capacity: manual pages that print such
        a value, each row with those lines.

        `also`: more keyword queries for the same question -- the AI's search
        terms after an Ask, so each tab of the right box shows what that
        question finds there (free: no AI call). Merged round-robin with `q`,
        like the answer's own retrieval, so one query can't crowd out another."""
        also = [a.strip()[:200] for a in also if a.strip()][:4]
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            full = scope_ids(vehicle, variant, engine)
            manual, parts = split_scope(full)
            scope = parts if tab == "parts" else manual
            # value lines naming only another engine or model are left out
            codes = query.vehicle_codes(vehicle, full)

            def other_engine(line: str) -> bool:
                if not codes:
                    return False
                engs, models = query.mentioned_codes(line, codes["families"], codes["series"])
                return bool((codes["engines"] and engs and not engs & codes["engines"])
                            or (codes["models"] and models and not models & codes["models"]))
            # the question box's own Search (not a tab's): kept in the history
            if record and tab == "workshop":
                _remember(request.state.who, "search", vehicle, variant, engine, None, q)
            if not scope:
                return []
            # the manual's wording first ("valve cover" -> "cylinder head
            # cover"), then the user's own words
            # (the parts catalogue's too: "sway bar" -> "anti-roll bar" in
            # WIS, "stabilizer" / "torsion bar" in EPC)
            extra = {"torque": " torque", "capacity": " capacity"}.get(tab, "")
            texts = list(dict.fromkeys(synonyms.translations(q) + [q] + also))
            per = []
            for query_text in texts:
                hits = search(conn, scope, query_text + extra, limit * 2,
                              include_other_variants=include_other_variants)
                for r in hits:
                    r["query"] = query_text
                per.append(hits)
            # with the AI's terms: round-robin across the queries; without,
            # the manual's wording then the user's, as before
            rows = ([r for group in zip_longest(*per) for r in group if r is not None]
                    if also else [r for hits in per for r in hits])
            # one row per page: a page whose two chunks both match is still
            # one result, and the keyword list showed it twice. A page counts
            # as seen only once a row for it is kept: its lines are picked by
            # the query that found it, so a query whose words are not in its
            # value lines must not hide the row another query would give
            seen, out = set(), []
            # a typed WIS document number ("AR20.10-P-1271A", or an old job
            # number "88-0220") is looked up, not searched: its page first,
            # the car's own version before other models' (numbers.py)
            if tab == "workshop" and _is_wis(vehicle):
                for printed in numbers.documents(q)[:3]:
                    for pg in wis_links.pages_for(conn, Path(vehicle["file_path"]), printed,
                                                  as_ids(manual), _corpus_ids(conn, vehicle))[:4]:
                        if pg["page_no"] in seen:
                            continue
                        seen.add(pg["page_no"])
                        out.append({"page_no": pg["page_no"], "doc_id": pg["doc_id"],
                                    "title": pg["title"], "section_path": pg["applies"],
                                    "applicability": "this_vehicle", "number": printed,
                                    "outside": not pg["in_scope"]})
            for r in rows:
                if r["page_no"] in seen:
                    continue
                if tab == "parts":
                    r["lines"] = part_lines(r.get("body") or "")
                    # the page's exploded drawings, whose printed callouts
                    # ("7C") are the lines' first column ("007C")
                    r["drawings"] = [i for i in (r.get("images") or "").split("\n") if i]
                elif tab in ("torque", "capacity"):
                    r["lines"] = value_lines(conn, r["page_uid"], tab, r["query"],
                                             other_engine=other_engine)
                    if not r["lines"]:
                        continue
                    # the tab is a table: what, then the value as printed
                    r["rows"] = [value_row(line, tab, r.get("title") or "") for line in r["lines"]]
                seen.add(r["page_no"])
                r.pop("body", None)
                out.append(r)
            return out[:limit]
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
            # the model picked in the question box says which provider answers;
            # none picked: the default (Shop setup). A model hidden from the
            # menu is refused, unless it is the default.
            chosen = body.model or config.active_model(cfg)
            provider = providers.provider_of(chosen)
            if provider is None:
                raise HTTPException(400, f"unknown model {chosen}")
            if chosen in (cfg.hidden_models or []) and chosen != config.active_model(cfg):
                raise HTTPException(400, f"{chosen} is turned off in Shop setup; pick another model.")
            # whose key pays: the company's or this mechanic's own, for that
            # provider; with none for them, a plain refusal -- never somebody
            # else's key, nor their key for another provider
            try:
                key = config.pick_key(cfg, who.user_id, who.admin, provider)
            except config.NoKeyForRequest as exc:
                raise HTTPException(400, str(exc))
            variant, engine, year, earlier = body.variant, body.engine, body.year, None
            if body.chat is not None:
                # a follow-up: only a chat this requester can see, about this
                # car; the car as the chat had it (the car box may have moved on)
                aconn = adb()
                try:
                    entry = history.get(aconn, body.chat, who)
                finally:
                    aconn.close()
                if entry is None or entry["kind"] != "ask" or entry["doc_id"] != vehicle["doc_id"]:
                    raise HTTPException(404, "That conversation is not in your history.")
                earlier = query.earlier_turns(entry)
                if len(earlier) > query.MAX_FOLLOWUPS:
                    raise HTTPException(400, f"This conversation has {query.MAX_FOLLOWUPS} follow-ups; "
                                             "start a new one for the next question.")
                variant, engine, year = entry["variant"], entry["engine"], entry["year"]
            try:
                result = query.ask(
                    conn, vehicle, body.question, key.key,
                    include_other_variants=body.include_other_variants,
                    model=chosen, source="web", variant=variant,
                    engine=engine, year=year,
                    user_id=who.user_id, user_name=who.name or None,
                    key_ref=key.ref, provider=provider, earlier=earlier)
            except (query.NoApiKey, query.ApiUnavailable) as exc:
                raise HTTPException(400, str(exc))
            out = result.as_dict()
            # kept whole: a paid answer can be opened again without paying; a
            # follow-up goes into its chat's entry
            if body.chat is not None:
                aconn = adb()
                try:
                    history.add_turn(aconn, body.chat, who, body.question, out)
                except (sqlite3.Error, TypeError, ValueError):
                    pass
                finally:
                    aconn.close()
                out["chat_id"] = body.chat
            else:
                out["chat_id"] = _remember(who, "ask", vehicle, variant, engine, year,
                                           body.question, out)
            return out
        finally:
            conn.close()

    def _remember(who, kind, vehicle, variant, engine, year, question, result=None):
        # never let the history break a good answer or search
        aconn = adb()
        try:
            return history.add(aconn, who, kind, history.pick(vehicle, variant, engine, year),
                               question, result)
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            aconn.close()

    @app.get("/api/history")
    def api_history(request: Request, limit: int = Query(history.SHOW, ge=1, le=200)) -> list[dict]:
        aconn = adb()
        try:
            return history.visible_to(aconn, request.state.who, limit)
        finally:
            aconn.close()

    @app.get("/api/history/{entry_id}")
    def api_history_entry(request: Request, entry_id: int) -> dict:
        aconn = adb()
        try:
            d = history.get(aconn, entry_id, request.state.who)
        finally:
            aconn.close()
        if d is None:     # someone else's or gone: all alike
            raise HTTPException(404, "No such entry in your history.")
        return d

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
            # a LEMON / folder HTML page can be shown as itself (/api/original)
            page["has_original"] = page.get("doc_type") != "parts" and (
                _is_wis(vehicle) or str(page.get("file_path") or "").lower().endswith((".html", ".htm")))
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

    @app.get("/api/values/{doc_id}")
    def api_values(doc_id: str, kind: str = Query(pattern="^(torque|capacity)$"),
                   pages: list[str] = Query(default=[]),
                   variant: int | None = None,
                   engine: str | None = Query(None, max_length=64)) -> list[dict]:
        """The Torque specs / Capacities tab for one job: the values on
        `pages` (the answer's pages, or the top search hits) and on the WIS
        procedures and value tables they link to, in this car's column only
        (tab_values.py). Free: no AI call."""
        pages = [p.strip()[:32] for p in pages if p.strip()][:tab_values.MAX_PAGES]
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            manual, _ = split_scope(scope_ids(vehicle, variant, engine))
            ids = _corpus_ids(conn, vehicle) if _is_wis(vehicle) else []
            return tab_values.job_values(conn, vehicle, manual, pages, kind, ids)
        finally:
            conn.close()

    @app.get("/api/parts/diagrams/{doc_id}")
    def api_parts_diagrams(request: Request, doc_id: str, variant: int | None = None,
                           engine: str | None = Query(None, max_length=64)) -> Response:
        """The car's parts catalogue as the catalogue reads: chassis, engines,
        options > group > subgroup, each with its page(s) and drawing count
        (parts_browse.py). The parts scope only, filtered like search. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            _, parts = split_scope(scope_ids(vehicle, variant, engine))
            rows = parts_browse.diagrams(conn, parts)
        finally:
            conn.close()
        return _etagged(request, json.dumps(rows, separators=(",", ":")).encode(),
                        "application/json")

    @app.get("/api/parts/procedures/{doc_id}")
    def api_part_procedures(doc_id: str, group: str = Query(pattern=r"^\w{1,4}$"),
                            name: str = Query(min_length=1, max_length=200),
                            variant: int | None = None,
                            engine: str | None = Query(None, max_length=64)) -> list[dict]:
        """The car's repair procedures for a parts subgroup: its own
        construction group's manual pages sharing a word with the name, then
        others sharing two (part_procedures.py). Matched by name, not a link
        the manual makes. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            manual, _ = split_scope(scope_ids(vehicle, variant, engine))
            return part_procedures.procedures(conn, vehicle, manual, _is_wis(vehicle), group, name)
        finally:
            conn.close()

    @app.get("/api/manual/contents/{doc_id}")
    def api_manual_contents(doc_id: str, variant: int | None = None,
                            engine: str | None = Query(None, max_length=64),
                            path: list[str] = Query([], max_length=8)) -> dict:
        """The car's manual as contents, one level at a time: WIS by function
        group and subgroup, other manuals by their own chapters
        (manual_browse.py). The manual scope only, filtered like search. Free."""
        path = [p[:300] for p in path]
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            manual, _ = split_scope(scope_ids(vehicle, variant, engine))
            t = manual_browse.tree(conn, vehicle, manual, _is_wis(vehicle))
        finally:
            conn.close()
        n = manual_browse.node(t, path)
        if n is None:
            raise HTTPException(404, "no such section")
        return n

    @app.get("/api/fluids/{doc_id}")
    def api_fluids(doc_id: str, variant: int | None = None,
                   engine: str | None = Query(None, max_length=64)) -> list[dict]:
        """The car's main fluids, from WIS's capacity sheets, in this car's
        column only; nothing until a model or engine is chosen. Free."""
        conn = db()
        try:
            vehicle = _vehicle_or_404(conn, doc_id)
            if variant is None and not engine:
                return []
            manual, _ = split_scope(scope_ids(vehicle, variant, engine))
            return tab_values.fluids(conn, vehicle, manual, _corpus_ids(conn, vehicle))
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
            owner = (_vehicle_or_404(conn, page["doc_id"]) if page and page.get("doc_id") != doc_id
                     else vehicle)
        finally:
            conn.close()
        from urllib.parse import quote
        if page and page.get("doc_type") != "parts" and not _is_wis(owner) \
                and str(page.get("file_path") or "").lower().endswith((".html", ".htm")):
            # a LEMON / folder HTML page: its own HTML, cleaned (html_original.py)
            nonce = secrets.token_urlsafe(16)
            try:
                body = html_original.render(
                    Path(page["file_path"]), Path(owner.get("file_path") or ""),
                    page.get("title") or "",
                    lambda f: f"/api/image/{quote(owner['doc_id'])}/{quote(f)}", nonce)
            except (html_original.NotAvailable, OSError) as exc:
                why = (str(exc) if request.state.who.admin
                       else "The original layout isn't available for this page.")
                return HTMLResponse(UNAVAILABLE.format(
                    why.replace("&", "&amp;").replace("<", "&lt;")), 404)
            return HTMLResponse(body, headers={
                "Content-Security-Policy": HTML_ORIGINAL_CSP.format(nonce)})
        if not page or not page_no.isdigit() or not re.match(
                r"mercedes-wis", vehicle.get("source_name") or ""):
            return HTMLResponse(UNAVAILABLE.format("There is no original layout for this page."), 404)
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
    def _image_path(request: Request, doc_id: str, name: str) -> Path:
        """The picture's file on disk -- the car's own images, or a joined
        parts catalogue's -- never outside them; 404 otherwise."""
        # a NUL (x.png%00.db) made Path.resolve() raise: an unhandled 500,
        # without the security headers. No picture is named that way.
        if "\x00" in name or "\x00" in doc_id:
            raise HTTPException(404, "image not found")
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
            try:
                other = (Path(fp) / "images").resolve()
                hit = (other / name).resolve()
                hit.relative_to(other)      # never outside that images directory
            except (OSError, ValueError):
                continue
            if hit.is_file() and hit.suffix.lower() in IMAGE_TYPES \
                    and not (Path(vehicle["file_path"]) / "images" / name).is_file():
                return hit
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
        # never serve outside the vehicle's own images directory; a name the
        # OS can't even resolve is not one of its pictures either
        try:
            target = (images_dir / name).resolve()
            target.relative_to(images_dir)
        except (OSError, ValueError):
            raise HTTPException(404, "image not found")
        if not target.is_file() or target.suffix.lower() not in IMAGE_TYPES:
            raise HTTPException(404, "image not found")
        return target

    @app.get("/api/image/{doc_id}/{name:path}")
    def api_image(request: Request, doc_id: str, name: str) -> FileResponse:
        return FileResponse(_image_path(request, doc_id, name), headers=IMAGE_HEADERS)

    @app.get("/api/callouts/{doc_id}/{name:path}")
    def api_callouts(request: Request, doc_id: str, name: str) -> list[dict]:
        """Where each callout number sits on a parts drawing, in its pixels:
        the extractor's <drawing>.callouts.json beside the PNG (from the
        drawing's own annotation layer). [] when the drawing has none or was
        pulled before callouts were saved."""
        side = _image_path(request, doc_id, name).with_suffix(".callouts.json")
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for c in data if isinstance(data, list) else []:
            if (isinstance(c, dict) and isinstance(c.get("x"), int) and isinstance(c.get("y"), int)
                    and isinstance(c.get("c"), str) and 0 < len(c["c"]) <= 8):
                out.append({"x": c["x"], "y": c["y"], "c": c["c"]})
        return out

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

    # -- cars waiting at the desk (handoff.py) -------------------------------
    # A phone sends the car it picked; the PC (and the mechanic's own other
    # devices) list it and open it with one click. Behind the same sign-in as
    # everything else: with sign-in required, a device not signed in is 401
    # in who_is_asking before it gets here.
    send_limit = handoff.SendLimit()

    @app.get("/api/handoff")
    def api_handoff_list(request: Request) -> dict:
        who: auth.Who = request.state.who
        aconn = adb()
        try:
            cars = handoff.visible_to(aconn, who)
        finally:
            aconn.close()
        # "shared": not signed in, so the list is everyone's who isn't either
        return {"cars": cars, "hours": handoff.TTL_HOURS,
                "shared": not who.admin and who.user_id is None}

    @app.post("/api/handoff")
    def api_handoff_send(request: Request, body: HandoffBody) -> dict:
        who: auth.Who = request.state.who
        if who.admin:
            raise HTTPException(400, "This PC is the desk; send a car from a phone.")
        sender = f"user:{who.user_id}" if who.user_id is not None else (
            "addr:" + (request.client.host if request.client else ""))
        if not send_limit.allow(sender):
            raise HTTPException(429, "Too many cars sent. Wait a few minutes.")
        conn = db()
        try:
            vehicles = index.vehicles(conn)
        finally:
            conn.close()
        try:
            pick = handoff.check_pick(vehicles, body.doc_id, body.variant, body.engine,
                                      body.year, body.vin)
        except handoff.BadPick as exc:
            raise HTTPException(400, str(exc))
        aconn = adb()
        try:
            new_id = handoff.add(aconn, pick, who)
        finally:
            aconn.close()
        return {"ok": True, "id": new_id, "label": pick["label"]}

    @app.post("/api/handoff/{handoff_id}/dismiss")
    def api_handoff_dismiss(request: Request, handoff_id: int) -> dict:
        aconn = adb()
        try:
            done = handoff.dismiss(aconn, handoff_id, request.state.who)
        finally:
            aconn.close()
        if not done:   # someone else's, already gone, or never there: all alike
            raise HTTPException(404, "No such car waiting.")
        return {"ok": True}

    @app.get("/api/models")
    def models(request: Request, menu: bool = False) -> list[dict]:
        """The default provider's models (Shop setup's default-model list);
        with `menu`, the question box's menu instead -- every model of every
        provider this requester has a key for, less the hidden ones, the
        default first (config.menu_models); with no key at all, the default
        provider's, so the menu still says what Ask would use. Price None =
        "cost not tracked"."""
        cfg = config.load()
        if not menu:
            return providers.model_list(config.active_provider(cfg))
        who: auth.Who = request.state.who
        ids = config.menu_models(cfg, who.user_id, who.admin)
        if not ids:
            return providers.model_list(config.active_provider(cfg))
        rows = {m["id"]: m for pid in providers.PROVIDERS for m in providers.model_list(pid)}
        return [{**rows[m], "provider_short": providers.PROVIDERS[rows[m]["provider"]]["short"]}
                for m in ids]

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

    # The car box's "Not downloaded" suggestions: LEMON catalogue cars the
    # index does not hold (lemon_catalogue.search). Any device that can search
    # sees them -- the list is the torrent's public index -- but only this PC
    # adds one (/api/import/lemon-add, admin).
    @app.get("/api/catalogue/suggest")
    def api_catalogue_suggest(q: str = Query("", max_length=200)) -> dict:
        return lemon_catalogue.search(q, app.state.index_path)

    @app.post("/api/import/lemon-add")
    def api_lemon_add(body: LemonPickBody) -> dict:
        """"Add this car" from the car box: the same import job as Shop setup's
        LEMON folder import when the car's folder is on this PC beside the LEMON
        manuals already imported; otherwise the car goes on Shop setup's LEMON
        download list (the download itself is not built yet)."""
        if len(body.path) != 4:
            raise HTTPException(400, "path must name one car: make, year, model, variant")
        cat = lemon_catalogue.load()
        if lemon_catalogue._node(cat, tuple(body.path)) is None:
            raise HTTPException(404, "Not in the LEMON catalogue: " + " / ".join(body.path))
        doc = lemon_catalogue.doc_id(*body.path)
        found = importer_jobs.lemon_on_disk(doc, app.state.index_path)
        if found:
            root, folder = found
            try:
                job = jobs.start(root, app.state.index_path, "lemon", only=[folder])
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
            return {"action": "import", "doc_id": doc, "folder": str(root / folder), "job": job}
        lemon_catalogue.set_pick(tuple(body.path), True)
        return {"action": "queued", "doc_id": doc}

    @app.get("/api/import/manuals")
    def api_import_manuals() -> dict:
        return importer_jobs.manuals(app.state.index_path)

    @app.get("/api/vms")
    def api_vms() -> dict:
        return importer_jobs.list_vms()

    # the VM is chosen as a file (its .vbox), browsed like an import folder
    @app.get("/api/vms/browse")
    def api_vm_browse(path: str = Query("", max_length=1000)) -> dict:
        if _local_path_error(path):
            raise HTTPException(400, _local_path_error(path))
        try:
            return importer_jobs.browse_vbox(path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/vms/pick")
    def api_vm_pick(body: VmPickBody) -> dict:
        if _local_path_error(body.file):
            raise HTTPException(400, _local_path_error(body.file))
        try:
            return importer_jobs.pick_vm(body.file)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))

    WIS_STEPS = ("docs", "applic", "vehicles", "images", "build")

    def vm_steps(what: str, steps: str) -> str:
        """The extractor steps a WIS / EPC / both run would take."""
        wis = [s for s in steps.split(",") if s] or list(WIS_STEPS)
        if any(s not in WIS_STEPS for s in wis):
            raise HTTPException(400, f"unknown step in {steps!r}")
        return ",".join({"wis": wis, "epc": ["epc"], "both": wis + ["epc"]}[what])

    @app.get("/api/vms/check")
    def api_vm_check(vm: str = Query(max_length=200),
                     what: str = Query("wis", pattern="^(wis|epc|both)$"),
                     steps: str = Query("", max_length=200)) -> dict:
        """Will a run on this VM work? Checked without booting it."""
        return importer_jobs.check_vm(vm, vm_steps(what, steps))

    @app.get("/api/vms/snapshots")
    def api_vm_snapshots(vm: str = Query(max_length=200)) -> dict:
        return importer_jobs.list_snapshots(vm)

    @app.get("/api/import")
    def api_import_status() -> dict:
        return jobs.status()

    @app.post("/api/import/cancel")
    def api_import_cancel() -> dict:
        try:
            return jobs.cancel()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/import/screen")
    def api_import_screen() -> FileResponse:
        """The picture of the VM's screen the extractor took when it stopped."""
        p = jobs.screen_path()
        if not p:
            raise HTTPException(404, "No picture of the VM's screen.")
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/import")
    def api_import_start(body: ImportBody) -> dict:
        if body.mode == "vm_rollback":
            # roll the VM back to one of its snapshots (asked for by a person;
            # the extractor snapshots the current state first)
            snaps = importer_jobs.list_snapshots(body.vm)
            if snaps.get("error"):
                raise HTTPException(400, snaps["error"])
            if body.snapshot not in {s["name"] for s in snaps["snapshots"]}:
                raise HTTPException(400, f"{body.vm} has no snapshot named {body.snapshot!r}.")
            try:
                return jobs.start(Path("."), app.state.index_path, "vm_rollback", vm=body.vm,
                                  steps=body.snapshot)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if body.mode in ("vm", "vm_epc", "vm_both", "vm_check"):
            info = importer_jobs.list_vms()
            if info.get("error"):
                raise HTTPException(400, str(info["error"]))
            if body.vm not in {v["name"] for v in info["vms"]}:
                raise HTTPException(400, f"No VirtualBox VM named {body.vm!r}.")
            # what the run needs is checked first (the extractor's --check):
            # a missing network, Java, script or disk space stops it here,
            # not an hour in
            what = {"vm": "wis", "vm_epc": "epc", "vm_both": "both"}.get(body.mode)
            check_steps = vm_steps(what, body.steps) if what else (body.steps or vm_steps("wis", ""))
            pre = importer_jobs.check_vm(body.vm, check_steps)
            if not pre["ready"]:
                raise HTTPException(400, "Not ready: " + " ".join(
                    c["message"] for c in pre["checks"] if c["level"] == "fail"))
        if body.mode == "vm_check":
            # boot the VM, see Windows start and the database(s) answer, shut it
            # down; imports nothing
            try:
                return jobs.start(Path(info["out_root"]) / "wis_extract", app.state.index_path,
                                  "vm_check", vm=body.vm, steps=check_steps)
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
        if body.mode in ("vm", "vm_both"):
            steps = [s for s in body.steps.split(",") if s]
            if any(s not in WIS_STEPS for s in steps):
                raise HTTPException(400, f"unknown step in {body.steps!r}")
        if body.mode == "vm_both":
            # WIS, then EPC, as one job: refused up front if the EPC half could
            # not run, rather than failing hours later after the WIS half
            problem = importer_jobs.epc_problem(importer_jobs.find_extractor())
            if problem:
                raise HTTPException(400, problem)
            try:
                return jobs.start(Path(info["out_root"]) / "wis_extract", app.state.index_path,
                                  "vm_both", vm=body.vm, steps=",".join(steps))
            except RuntimeError as exc:
                raise HTTPException(409, str(exc))
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
                                  only=body.vehicles if body.mode == "remove" else None)
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
            raise HTTPException(400, "No Mercedes WIS data in the index to rebuild; add it instead.")
        try:
            return jobs.start(p, app.state.index_path, body.mode)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/settings")
    def get_settings() -> dict:
        cfg = config.load()
        conn = db()
        try:
            month_q, month_usd = index.month_total(conn)
        finally:
            conn.close()
        active = config.active_provider(cfg)
        key = config.company_key(cfg, active)
        env = providers.PROVIDERS[active]["env_key"]
        saved = cfg.api_key if active == "anthropic" else (cfg.provider_keys or {}).get(active)
        return {
            "key_mode": cfg.key_mode,
            "provider": active,
            # each provider's key page and whether its company key is set --
            # never the key; the last 4 only
            "providers": [
                {"id": pid, "label": p["label"], "short": p["short"],
                 "key_url": p["key_url"], "key_hint": p["key_hint"],
                 "price_source": p["price_source"],
                 "has_key": bool(config.company_key(cfg, pid)),
                 "key_last4": config.key_last4(config.company_key(cfg, pid)),
                 "key_source": ("config" if (cfg.api_key if pid == "anthropic"
                                             else (cfg.provider_keys or {}).get(pid))
                                else "environment" if config.company_key(cfg, pid) else "none"),
                 "key_env": p["env_key"],
                 # the question box's menu: each model, and whether it is hidden
                 "models": [{"id": m, "label": p["models"][m]["label"],
                             "hidden": m in (cfg.hidden_models or []),
                             "added": bool(p["models"][m].get("added"))} for m in p["models"]]}
                for pid, p in providers.PROVIDERS.items()],
            "hidden_models": list(cfg.hidden_models or []),
            # everyone, every source, this UTC month: "One company key" mode's total
            "month_questions": month_q,
            "month_spend_usd": round(month_usd, 4),
            "company_key_last4": config.key_last4(key),
            "archive_dir": cfg.archive_dir,
            "index_path": app.state.index_path,  # the one in use, not the saved one
            "importer": cfg.importer,
            "model": config.active_model(cfg),
            "extractor_dir": cfg.extractor_dir,
            "has_api_key": bool(key),
            "key_source": ("config" if saved else "environment" if key else "none"),
            "key_env": env,
            # None: not offered on this OS
            "autostart": autostart.is_enabled() if autostart.supported() else None,
            "phone_urls": _phone_urls(),
            "require_signin": cfg.require_signin,
            "show_damage_codes": cfg.show_damage_codes,
            "config_path": str(config.CONFIG_PATH),
        }

    @app.get("/api/settings/models/{provider}")
    def check_models(provider: str) -> dict:
        """What the provider's own model list offers that is not in the menu
        yet (providers.list_models, on the company key): one GET, no tokens,
        nothing spent. Admin only (ADMIN_PREFIXES)."""
        if provider not in providers.PROVIDERS:
            raise HTTPException(404, "Unknown AI provider.")
        cfg = config.load()
        try:
            listed = providers.list_models(provider, config.company_key(cfg, provider))
        except (providers.NoApiKey, providers.ApiUnavailable) as exc:
            raise HTTPException(400, str(exc))
        have = providers.PROVIDERS[provider]["models"]
        return {"listed": len(listed), "new": [m for m in listed if m["id"] not in have]}

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
        provider = body.provider or config.active_provider(config.load())
        if body.model is not None and body.model not in providers.PROVIDERS[provider]["models"]:
            raise HTTPException(400, f"Unknown model {body.model!r}.")
        if body.hidden_models is not None and (bad := [
                m for m in body.hidden_models if providers.provider_of(m) is None]):
            raise HTTPException(400, f"Unknown model {bad[0]!r}.")
        if body.extra_models is not None:
            for pid, ids in body.extra_models.items():
                if pid not in providers.PROVIDERS or len(ids) > 30:
                    raise HTTPException(400, f"Unknown AI provider {pid!r}.")
                for mid in ids:
                    other = providers.provider_of(mid)
                    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", mid) or (
                            other and not providers.PROVIDERS[other]["models"][mid].get("added")):
                        raise HTTPException(400, f"{mid!r} can't be added.")
        if body.api_key is not None and (not body.api_key.strip()
                                         or any(c.isspace() for c in body.api_key.strip())):
            raise HTTPException(400, "Paste the whole key, with no spaces.")
        if body.autostart is not None and body.autostart != autostart.is_enabled():
            try:
                autostart.set_enabled(body.autostart)
            except RuntimeError as exc:
                raise HTTPException(400, str(exc))
        cfg = config.load()
        for field_name in ("archive_dir", "index_path", "importer",
                           "model", "extractor_dir", "require_signin", "key_mode",
                           "provider", "show_damage_codes"):
            value = getattr(body, field_name)
            if value is not None:
                setattr(cfg, field_name, value)
        if body.provider is not None and body.model is None:
            # a model saved under the old provider means nothing to the new one
            cfg.model = config.active_model(cfg)
        if body.hidden_models is not None:
            cfg.hidden_models = list(dict.fromkeys(body.hidden_models))
        if body.extra_models is not None:
            cfg.extra_models = {pid: list(dict.fromkeys(ids))
                                for pid, ids in body.extra_models.items() if ids}
            providers.sync_added(cfg.extra_models)
        # the default is always in the menu
        cfg.hidden_models = [m for m in cfg.hidden_models or [] if m != config.active_model(cfg)]
        if body.api_key is not None:
            # a company key belongs to one provider; the Anthropic one stays `api_key`
            kp = body.key_provider or config.active_provider(cfg)
            if kp == "anthropic":
                cfg.api_key = body.api_key.strip()
            else:
                cfg.provider_keys = {**(cfg.provider_keys or {}), kp: body.api_key.strip()}
        cfg.save()
        search_mod.show_damage_codes = cfg.show_damage_codes
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
        cfg = config.load()
        conn = db()
        try:
            for u in users:
                q, c = spend.get(u["id"], (0, 0.0))
                u.update(month_questions=q, month_spend_usd=round(c, 4),
                         key=_key_status(conn, cfg, u["id"]))
        finally:
            conn.close()
        q, c = spend.get(None, (0, 0.0))
        return {"users": users, "unattributed": {"month_questions": q,
                                                 "month_spend_usd": round(c, 4)},
                "require_signin": cfg.require_signin,
                "key_mode": cfg.key_mode,
                "provider": config.active_provider(cfg),
                "phone_urls": _phone_urls()}

    @app.post("/api/admin/users")
    def admin_add_user(body: NewUserBody) -> dict:
        """Adds the mechanic; the page then opens their sign-in QR."""
        aconn = adb()
        try:
            return {"id": auth.add_user(aconn, body.name, body.monthly_cap_usd)}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            aconn.close()

    @app.get("/api/admin/site-qr")
    def admin_site_qr() -> dict:
        """A QR that opens AutoRundown on a phone, and the addresses to write
        under it. It holds the LAN address, not autorundown.local: a camera
        app on an Android phone often can't resolve .local names, while an
        address always works (a home-screen icon added from autorundown.local
        survives the PC getting a new address, so both are written out)."""
        base = _qr_base()
        return {"url": base, "svg": qr.svg(base + "/") if base else "",
                "urls": _phone_urls()}

    # -- sign-in QRs: one per dialog, single use, 15 minutes ---------------
    @app.post("/api/admin/users/{user_id}/qr")
    def admin_new_qr(user_id: int) -> dict:
        base = _qr_base()
        if not base:
            raise HTTPException(409, "This PC has no network address, so a phone can't reach it.")
        aconn = adb()
        try:
            g = auth.new_grant(aconn, user_id)
        except KeyError:
            raise HTTPException(404, "No such user.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            aconn.close()
        link = f"{base}/signin?t={g.link_token}"
        # the link and the code go to this page once and are not kept
        return {"grant_id": g.id, "svg": qr.svg(link), "url": link, "code": g.code,
                "expires_in": max(0, round(g.expires - time.time())),
                "minutes": auth.CODE_MINUTES, "site": base}

    def _grant_of(aconn, user_id: int, grant_id: int) -> None:
        row = aconn.execute("SELECT user_id FROM grants WHERE id=?", (grant_id,)).fetchone()
        if row is None or row["user_id"] != user_id:   # another mechanic's QR
            raise HTTPException(404, "No such sign-in QR for this mechanic.")

    @app.get("/api/admin/users/{user_id}/qr/{grant_id}")
    def admin_qr_status(user_id: int, grant_id: int) -> dict:
        """pending | used | voided | expired: the dialog closes itself on used."""
        aconn = adb()
        try:
            _grant_of(aconn, user_id, grant_id)
            return {"state": auth.grant_status(aconn, grant_id)}
        finally:
            aconn.close()

    @app.post("/api/admin/users/{user_id}/qr/{grant_id}/void")
    def admin_void_qr(user_id: int, grant_id: int) -> dict:
        """The dialog was closed: its QR and code stop working at once."""
        aconn = adb()
        try:
            _grant_of(aconn, user_id, grant_id)
            auth.void_grant(aconn, grant_id)
        finally:
            aconn.close()
        return {"ok": True}

    # -- "A key per mechanic" (admin only) --------------------------------
    @app.post("/api/admin/users/{user_id}/key")
    def admin_mechanic_key(user_id: int, body: MechanicKeyBody) -> dict:
        aconn = adb()
        try:
            if aconn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
                raise HTTPException(404, "No such user.")
        finally:
            aconn.close()
        cfg = config.load()
        # a mechanic's key is for one provider: the one answering now
        provider = config.active_provider(cfg)
        if body.remove:
            config.set_mechanic_entry(cfg, user_id, provider, None)
            cfg.save()
            return {"ok": True}
        entry = config.mechanic_entry(cfg, user_id, provider)
        new_key = (body.api_key or "").strip()
        if body.api_key is not None:
            if not new_key or any(c.isspace() for c in new_key):
                raise HTTPException(400, "Paste the whole key, with no spaces "
                                         f"(it starts {providers.PROVIDERS[provider]['key_hint']}).")
            entry["api_key"] = new_key
        if not entry.get("api_key"):
            raise HTTPException(400, "Add this mechanic's API key first.")
        if body.clear_loaded:
            entry["loaded_usd"] = None
        elif body.loaded_usd is not None:
            entry["loaded_usd"] = body.loaded_usd
        # a new key, or "topped up / new key": count spend from now
        if new_key or body.reset or not entry.get("since"):
            entry["since"] = _utc_now()
        entry.setdefault("loaded_usd", None)
        config.set_mechanic_entry(cfg, user_id, provider, entry)
        cfg.save()
        conn = db()
        try:
            return {"ok": True, "key": _key_status(conn, cfg, user_id)}
        finally:
            conn.close()

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

    def _qr_base() -> str:
        """The address a QR sends a phone to: this PC's LAN address (the one
        its default route uses, not a WSL/VPN adapter's), else the first
        address found; "" when there is none."""
        scheme = "https" if app.state.https else "http"
        ip = lan_ip()
        if not ip.startswith("127."):
            return f"{scheme}://{ip}:{app.state.port}"
        mdns = app.state.mdns_name
        urls = [u for u in _phone_urls() if not (mdns and f"//{mdns}:" in u)]
        return urls[0] if urls else ""

    def _key_status(conn, cfg: config.Config, user_id: int) -> dict:
        """A mechanic's own key as the admin may see it: never the key, only
        whether there is one and its last 4. Remaining is AutoRundown's own
        estimate: the credit the owner said was loaded minus what this app
        logged on that key since then (Anthropic does not tell a normal key
        its balance)."""
        provider = config.active_provider(cfg)
        e = config.mechanic_entry(cfg, user_id, provider)
        key = e.get("api_key") or ""
        spent = (index.key_spend_since(conn, config.key_ref(provider, user_id), e.get("since") or "")
                 if key else 0.0)
        loaded = e.get("loaded_usd")
        return {"has_key": bool(key), "last4": config.key_last4(key),
                "since": e.get("since") or None, "loaded_usd": loaded,
                "spent_usd": round(spent, 4),
                "remaining_usd": round(loaded - spent, 4) if key and loaded is not None else None}

    return app


def _utc_now() -> str:
    """usage_log.ts's own format (SQLite datetime('now'), UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
