"""Prices for a part from the shop's own suppliers, through PartsTech
(2026-11-09, the user: "One place to click or maybe even pull the cheapest
price").

PartsTech (partstech.com) is a parts-ordering site for repair shops: the shop
connects its supplier accounts (Worldpac, NAPA, O'Reilly, a local store...)
and one search shows each one's price -- the shop's own, after its discounts
-- and stock. Its API (published OpenAPI 3.0 spec, api.partstech.com,
checked 2026-11-09) is for software partners: AutoRundown signs in with
PartsTech's partner ID and key *and* the shop's PartsTech user ID and key
(POST /oauth/access, accessType "user"), lists the shop's stores (GET
/profile/shop/suppliers, only those PartsTech has approved), and asks each
store for one part number (POST /catalog/quote, PartNumberSearchParams: one
number per request, `storeId` required). Each quoted part carries `price`
(PartPrice: `price` = what the shop pays, discounts and core charge
included; `cost` without the core; `core`; `list`, often null), `status`
(IN_STOCK...), `quantity` and `storesAvailability`.

**Not live yet.** The partner ID and key come from PartsTech under a signed
agreement (asked for 2026-11-09; there is no sandbox). Until then this runs
only against tools/fake_partstech.py. Their terms, as published
(partstech.com/api-agreement-terms), that this code keeps:
  - nothing from PartsTech is stored: no price, part or store list is
    cached, on disk or in memory (only the access token, which is a
    credential, until it expires);
  - the shop's users see it, nobody else (`/api/prices` is behind the same
    sign-in as search);
  - which of the shop's own stores is cheapest is worked out per request,
    for that user -- asked in the e-mail whether that is fine.
The terms also make PartsTech the app's only aftermarket parts catalogue;
whether the plain supplier links (suppliers.py) count is asked too.

Keys live in Config.partstech, are never sent to a phone (the admin sees
the last 4) and are never logged. `PARTSTECH_BASE_URL` points it elsewhere
(dev.py --fake-api: the fake).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_BASE = "https://api.partstech.com"
TIMEOUT = 20          # a person is waiting on the parts list
MAX_STORES = 12       # more connected stores than this: the first 12 by priority
FIELDS = ("partner_id", "partner_key", "user_id", "user_key")
# PartsTech's own words for a quoted part's availability, for a person
STATUS_TEXT = {"IN_STOCK": "In stock", "OUT_OF_STOCK": "Out of stock",
               "SPECIAL_ORDER": "Special order", "BACK_ORDER": "Back order",
               "CHECK_AVAILABILITY": "Check availability"}


class NotSetUp(RuntimeError):
    """No PartsTech keys in Shop setup."""


class Unavailable(RuntimeError):
    """PartsTech refused or didn't answer, in words a person can act on."""


def base_url() -> str:
    return (os.environ.get("PARTSTECH_BASE_URL") or DEFAULT_BASE).rstrip("/")


def creds(cfg) -> dict | None:
    """The four values from Shop setup, or None if any is missing."""
    saved = getattr(cfg, "partstech", None) or {}
    out = {f: str(saved.get(f) or "").strip() for f in FIELDS}
    return out if all(out.values()) else None


def clean_part_number(pn: str) -> str:
    """What is sent: the number as the parts list gives it (pnDigits), less
    spaces. Letters, digits and - . / only -- anything else is refused, so a
    request is never built from odd text."""
    s = "".join(str(pn or "").split())
    if not s or len(s) > 40 or any(not (c.isalnum() or c in "-./") for c in s):
        raise ValueError("Not a part number.")
    return s


# -- HTTP ----------------------------------------------------------------------

def _request(method: str, path: str, body: dict | None = None, token: str | None = None):
    url = base_url() + path
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        raise _friendly(exc.code, text) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        raise Unavailable("Could not reach PartsTech. Check the network connection.") from None
    except ValueError:
        raise Unavailable("PartsTech sent a reply that is not JSON.") from None


def _message(text: str) -> str:
    try:
        err = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    if isinstance(err, dict):
        for k in ("message", "error_description", "error", "detail", "title"):
            if isinstance(err.get(k), str):
                return err[k][:200]
    return ""


class _Status(Unavailable):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def _friendly(status: int, text: str) -> Unavailable:
    why = _message(text)
    if status in (401, 403):
        return _Status(status, "PartsTech refused the keys in Shop setup → Parts suppliers"
                       + (f" ({why})" if why else "") + ". Check the partner and user ID and key.")
    if status == 429:
        return _Status(status, "PartsTech says too many requests right now. Try again in a minute.")
    if status >= 500:
        return _Status(status, f"PartsTech had a problem (error {status}). Try again later.")
    return _Status(status, f"PartsTech refused the request (error {status})" + (f": {why}" if why else "."))


# -- access token: the one thing kept, and only until it expires --------------

_TOKENS: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


def _cache_key(c: dict) -> str:
    return hashlib.sha256("\0".join(c[f] for f in FIELDS).encode()).hexdigest()


def token(c: dict) -> str:
    """A user access token (60 minutes, per the spec), reused until a minute
    before it runs out."""
    key = _cache_key(c)
    with _TOKEN_LOCK:
        hit = _TOKENS.get(key)
        if hit and hit[1] > time.time():
            return hit[0]
    got = _request("POST", "/oauth/access", {
        "accessType": "user",
        "credentials": {"user": {"id": c["user_id"], "key": c["user_key"]},
                        "partner": {"id": c["partner_id"], "key": c["partner_key"]}}})
    tok = (got or {}).get("accessToken") if isinstance(got, dict) else None
    if not tok:
        raise Unavailable("PartsTech did not hand out an access token.")
    life = got.get("expiresIn")
    life = float(life) if isinstance(life, (int, float)) and life > 0 else 3600.0
    with _TOKEN_LOCK:
        _TOKENS[key] = (tok, time.time() + max(60.0, life - 60.0))
    return tok


def forget_tokens() -> None:
    """New keys saved: the next request signs in again."""
    with _TOKEN_LOCK:
        _TOKENS.clear()


def _authed(c: dict, method: str, path: str, body: dict | None = None):
    """A request with the token; a 401 (a token PartsTech dropped early) signs
    in once more and tries again."""
    tok = token(c)            # keys refused here: said at once, never tried twice
    try:
        return _request(method, path, body, tok)
    except _Status as exc:
        if exc.status != 401:
            raise
        forget_tokens()
        return _request(method, path, body, token(c))


# -- the shop's stores and a part's quotes -------------------------------------

def stores(c: dict) -> list[dict]:
    """The shop's connected stores PartsTech has approved, by the shop's own
    priority: [{"id", "name", "supplier"}]."""
    got = _authed(c, "GET", "/profile/shop/suppliers")
    out = []
    for pref in got if isinstance(got, list) else []:
        if not isinstance(pref, dict) or pref.get("status") != "Approved":
            continue
        store, sup = pref.get("store") or {}, pref.get("supplier") or {}
        if not isinstance(store.get("id"), int):
            continue
        out.append({"id": store["id"], "name": str(store.get("name") or ""),
                    "supplier": str(sup.get("name") or ""), "priority": pref.get("priority") or 0})
    out.sort(key=lambda s: -(s["priority"] if isinstance(s["priority"], int) else 0))
    return [{k: s[k] for k in ("id", "name", "supplier")} for s in out[:MAX_STORES]]


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _offer(part: dict, store: dict) -> dict | None:
    price = part.get("price") or {}
    total = _num(price.get("price"))
    cost, core = _num(price.get("cost")), _num(price.get("core"))
    if total is None and cost is not None:
        total = cost + (core or 0.0)
    if total is None or total < 0:
        return None          # no price is never shown as one
    brand = part.get("brand") or {}
    sup = part.get("supplier") or {}
    st = part.get("store") or {}
    qty = part.get("quantity")
    return {
        "supplier": str(sup.get("name") or store["supplier"]),
        "store": str(st.get("name") or store["name"]),
        "store_id": store["id"],
        "part_number": str(part.get("partNumber") or ""),
        "brand": str(brand.get("displayName") or brand.get("brandName") or ""),
        "name": str(part.get("partName") or ""),
        "price": round(total, 2),
        "core": round(core, 2) if core else 0.0,
        "list": _num(price.get("list")),
        "status": STATUS_TEXT.get(part.get("status"), str(part.get("status") or "")),
        "in_stock": part.get("status") == "IN_STOCK",
        "quantity": qty if isinstance(qty, int) else None,
        "url": str(part.get("partsTechCatalogURL") or ""),
    }


def quote(c: dict, part_number: str, store: dict) -> list[dict]:
    got = _authed(c, "POST", "/catalog/quote",
                  {"searchParams": {"partNumber": [part_number]}, "storeId": store["id"]})
    parts = (got or {}).get("parts") if isinstance(got, dict) else None
    return [o for p in parts or [] if isinstance(p, dict) and (o := _offer(p, store))]


def prices(c: dict, part_number: str) -> dict:
    """Every connected store's quotes for one part number, cheapest first.
    {"part_number", "offers": [...], "stores": n, "problems": ["<store>: why"]}.
    A store that fails doesn't stop the others; nothing is kept."""
    pn = clean_part_number(part_number)
    shop = stores(c)
    offers, problems = [], []
    if shop:
        with ThreadPoolExecutor(max_workers=min(4, len(shop))) as pool:
            for store, res in zip(shop, pool.map(lambda s: _safe_quote(c, pn, s), shop)):
                if isinstance(res, Exception):
                    problems.append(f"{store['supplier'] or store['name']}: {res}")
                else:
                    offers += res
    # in stock first is the person's choice to make; cheapest first is ours
    offers.sort(key=lambda o: (o["price"], not o["in_stock"]))
    return {"part_number": pn, "offers": offers, "stores": len(shop), "problems": problems}


def _safe_quote(c, pn, store):
    try:
        return quote(c, pn, store)
    except Unavailable as exc:
        return exc
