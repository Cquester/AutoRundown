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
import re
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import auth, config, index, query, tls
from . import autostart, importer_jobs, lemon_catalogue, synonyms, wis_original
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
    extractor_dir: str | None = None
    # Windows: start in the background at sign-in (autostart.py)
    autostart: bool | None = None
    require_signin: bool | None = None


class SigninBody(BaseModel):
    code: str


class NewUserBody(BaseModel):
    name: str
    monthly_cap_usd: float | None = None


class UserUpdateBody(BaseModel):
    disabled: bool | None = None
    monthly_cap_usd: float | None = None
    clear_cap: bool = False


class LemonPicksBody(BaseModel):
    # doc_ids from /api/import/lemon-catalogue rows
    doc_ids: list[str]
    picked: bool = True


class ImportBody(BaseModel):
    path: str
    # add: ingest.py (resumable, keeps what is there)
    # rebuild_wis: rebuild_wis.py (replace the Mercedes WIS part, keep the rest)
    mode: str = "add"
    # mode "vm": extract from this VirtualBox VM first (vm_pipeline.py)
    vm: str = ""
    steps: str = ""
    # mode "lemon": the vehicle folders to import, relative to path
    vehicles: list[str] = []


def _vehicle_or_404(conn, doc_id: str) -> dict:
    for v in index.vehicles(conn):
        if v["doc_id"] == doc_id:
            return v
    raise HTTPException(404, f"unknown vehicle {doc_id}")


# answer to any device, signed in or not: the page itself, so it can show
# the sign-in screen, and the CA a phone must install before trusting HTTPS
PUBLIC_PATHS = {"/", "/manifest.webmanifest", "/ca.crt", "/api/me",
                "/api/auth/signin", "/api/auth/signout"}
# answer to the PC running the server only -- they read and change this PC's
# files, settings and API key, and manage accounts
ADMIN_PREFIXES = ("/api/settings", "/api/browse", "/api/vms", "/api/import",
                  "/api/admin/", "/api/shutdown")


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


def create_app(index_path: str, auth_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="AutoRundown")
    app.state.index_path = index_path
    app.state.auth_path = auth_path or auth.AUTH_PATH
    app.state.https = False
    app.state.port = 8756
    throttle = auth.Throttle()
    app.state.mdns_name = ""          # serve() sets it once announced
    app.state.shutdown = lambda: None   # make_server() wires the real one

    def db():
        # SQLite connections are per-thread; make a fresh one per request.
        return index.connect(app.state.index_path)

    def adb():
        return auth.connect(app.state.auth_path)

    @app.middleware("http")
    async def who_is_asking(request: Request, call_next):
        path = request.url.path
        addr = request.client.host if request.client else ""
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
        if (path not in PUBLIC_PATHS and who is None
                and config.load().require_signin):
            return JSONResponse({"detail": "Sign in to use AutoRundown.",
                                 "signin": True}, 401)
        return await call_next(request)

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
    # run a long job, so they answer only to the machine the server runs on
    # (ADMIN_PREFIXES, enforced in who_is_asking) -- never to a phone on the
    # LAN, which the server otherwise welcomes.

    @app.post("/api/shutdown")
    def api_shutdown() -> dict:
        app.state.shutdown()
        return {"ok": True}

    @app.get("/api/browse")
    def api_browse(path: str = "") -> dict:
        p = Path(path).expanduser() if path else Path(config.load().archive_dir or Path.home())
        if not p.is_dir():
            p = Path.home()
        try:
            dirs = sorted((c.name for c in p.iterdir()
                           if c.is_dir() and not c.name.startswith(".")), key=str.lower)
        except OSError:
            dirs = []
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
                "dirs": dirs[:500], "detected": importer_jobs.describe(p, app.state.index_path)}

    @app.get("/api/import/lemon")
    def api_import_lemon(path: str = "") -> dict:
        p = Path(path).expanduser() if path else Path(config.load().archive_dir or Path.home())
        return importer_jobs.lemon_vehicles(p, app.state.index_path)

    @app.get("/api/import/lemon-catalogue")
    def api_lemon_catalogue(make: str = "", year: str = "", model: str = "") -> dict:
        return lemon_catalogue.browse(app.state.index_path, make, year, model)

    @app.post("/api/import/lemon-catalogue/picks")
    def api_lemon_picks(body: LemonPicksBody) -> dict:
        if len(body.doc_ids) > 5000:
            raise HTTPException(400, "Too many vehicles in one request.")
        return {"picked": lemon_catalogue.set_picks(body.doc_ids, body.picked)}

    @app.get("/api/vms")
    def api_vms() -> dict:
        return importer_jobs.list_vms()

    @app.get("/api/import")
    def api_import_status() -> dict:
        return jobs.status()

    @app.post("/api/import")
    def api_import_start(body: ImportBody) -> dict:
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
        }

    @app.post("/api/settings")
    def post_settings(body: SettingsBody) -> dict:
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
