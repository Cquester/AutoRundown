"""A stand-in for the PartsTech API, for test boxes, dev.py and the tests -- FREE.

    python tools/fake_partstech.py [--port 8759]

Answers the three calls autorundown/partstech.py makes, in the shapes of
PartsTech's published OpenAPI spec (api.partstech.com, version 2.021.1):

    POST /oauth/access              user + partner ID/key -> accessToken
    GET  /profile/shop/suppliers    the shop's stores (one not approved yet)
    POST /catalog/quote             one part number at one store -> parts

dev.py --fake-api serves it from the fake AI's port under /partstech
(fake_anthropic.py hands those paths here) and points PARTSTECH_BASE_URL at
it. Every price is made up from the part number and the store, the same
each time, and every part is named "[fake PartsTech] ..." and branded FAKE,
so a screenshot can never pass for a real price.

Keys: any non-empty ID and key work, except a key "bad" (401). Part
numbers pick the rare paths:

    ...0000      (ends in four zeros) no store has it
    1111111111   the second store fails (500); the others answer
    2222222222   every store answers 429 (too many requests)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "/partstech"
STORES = [
    {"id": 101, "status": "Approved", "priority": 5,
     "supplier": {"id": 1, "name": "Worldpac"}, "store": {"id": 101, "name": "Worldpac Hartford", "sellerId": None}},
    {"id": 102, "status": "Approved", "priority": 4,
     "supplier": {"id": 2, "name": "NAPA"}, "store": {"id": 102, "name": "NAPA Main St", "sellerId": None}},
    {"id": 103, "status": "Approved", "priority": 3,
     "supplier": {"id": 3, "name": "O'Reilly"}, "store": {"id": 103, "name": "O'Reilly Route 9", "sellerId": None}},
    # not approved by PartsTech yet: the app must not ask it
    {"id": 104, "status": "Pending", "priority": 2,
     "supplier": {"id": 4, "name": "Pending Parts"}, "store": {"id": 104, "name": "Pending Parts", "sellerId": None}},
]
REQUESTS: list[dict] = []     # what was asked, for the tests (never a key's value)


def _price(pn: str, store_id: int) -> float:
    h = int(hashlib.sha256(f"{pn}|{store_id}".encode()).hexdigest()[:8], 16)
    return round(12 + (h % 9000) / 100, 2)


def _part(pn: str, store: dict, brand: str, extra: float = 0.0, status: str = "IN_STOCK") -> dict:
    total = round(_price(pn, store["id"]) + extra, 2)
    core = 5.0 if pn.endswith("5") else 0.0
    return {
        "partId": f"FAKE-{pn}", "partNumber": pn, "partName": f"[fake PartsTech] part {pn}",
        "brand": {"brandID": "FAKE", "brandName": brand, "displayName": brand},
        "partsTechCatalogURL": f"https://example.invalid/fake-partstech/{pn}",
        "status": status, "supplier": {"id": store["supplier"]["id"], "name": store["supplier"]["name"],
                                       "allowStoreSelect": True, "availabilitySelect": True},
        "store": {"id": store["store"]["id"], "name": store["store"]["name"]},
        "lineCardId": 1,
        "price": {"price": total, "cost": round(total - core, 2), "core": core, "fet": 0, "list": round(total * 1.4, 2)},
        "quantity": 3 if status == "IN_STOCK" else 0,
        "storesAvailability": [{"specificName": "In network", "store": {"id": store["store"]["id"],
                                "name": store["store"]["name"]}, "quantity": 3, "main": True}],
    }


def handle(method: str, path: str, headers, body: dict | None) -> tuple[int, object]:
    """(status, JSON body) for a request whose path starts after PREFIX."""
    REQUESTS.append({"method": method, "path": path, "body": _no_keys(body)})
    if method == "POST" and path == "/oauth/access":
        cred = (body or {}).get("credentials") or {}
        user, partner = cred.get("user") or {}, cred.get("partner") or {}
        if (body or {}).get("accessType") != "user" or not all(
                (x.get("id") and x.get("key")) for x in (user, partner)):
            return 400, {"message": "accessType user needs user and partner id and key"}
        if "bad" in (user.get("key"), partner.get("key")):
            return 401, {"message": "Invalid credentials"}
        return 200, {"tokenType": "Bearer", "accessToken": f"fake-token-{user['id']}",
                     "expiresIn": 3600, "username": user["id"], "partner": partner["id"]}
    auth = headers.get("Authorization") or ""
    if not auth.startswith("Bearer fake-token-"):
        return 401, {"message": "Invalid token"}
    if method == "GET" and path == "/profile/shop/suppliers":
        return 200, [dict(s, credentials=[], statusDescription="") for s in STORES]
    if method == "POST" and path == "/catalog/quote":
        sp = (body or {}).get("searchParams") or {}
        nums, sid = sp.get("partNumber"), (body or {}).get("storeId")
        if not (isinstance(nums, list) and len(nums) == 1 and isinstance(sid, int)):
            return 400, {"message": "searchParams.partNumber (one) and storeId are required"}
        store = next((s for s in STORES if s["store"]["id"] == sid), None)
        if store is None:
            return 400, {"message": "unknown store"}
        pn = str(nums[0])
        if pn == "2222222222":
            return 429, {"message": "Too many requests"}
        if pn == "1111111111" and sid == 102:
            return 500, {"message": "Internal server error"}
        if pn.endswith("0000"):
            return 200, {"parts": [], "filters": [], "errors": None}
        parts = [_part(pn, store, "FAKE OE")]
        if sid == 103:      # a second brand at one store, out of stock
            parts.append(_part(pn, store, "FAKE Aftermarket", extra=-4.0, status="OUT_OF_STOCK"))
        return 200, {"parts": parts, "filters": [], "errors": None}
    return 404, {"message": f"fake PartsTech: no route {method} {path}"}


def _no_keys(body):
    if not isinstance(body, dict) or "credentials" not in body:
        return body
    return {**body, "credentials": "(not kept)"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"fake_partstech: {fmt % args}", flush=True)

    def _go(self, method):
        path = self.path.split("?")[0]
        if path.startswith(PREFIX):
            path = path[len(PREFIX):]
        body = None
        if method == "POST":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        status, out = handle(method, path, self.headers, body)
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8759)
    port = ap.parse_args().port
    print(f"fake PartsTech on http://127.0.0.1:{port}{PREFIX}", flush=True)
    serve(port).serve_forever()


if __name__ == "__main__":
    main()
