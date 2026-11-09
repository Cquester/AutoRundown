"""Where a part can be looked up: each supplier's own part-number search, as
a link the user's browser opens (2026-11-08, the user: "We can change what
they look at depending on the car they look at").

The app never asks a supplier anything. None of them offers prices to an
app without a partner agreement (FCP Euro, ECS Tuning and RockAuto have no
public API; Pelican Parts and ECS turn automated requests away), so a
supplier here is only a name, a search address with `{pn}` where the part
number goes, and the makes it is shown for. The parts list shows, for each
part, the suppliers whose makes fit that part's car; Shop setup edits the
list (`Config.suppliers`; None = DEFAULTS).

The part number goes in as the browser's pnDigits gives it: a Mercedes
number without its letter or spaces ("A 001 420 81 20" -> 0014208120).
Checked by hand on 2026-11-08 with that front pad: FCP Euro, ECS Tuning
and RockAuto all find it from the digits. Worldpac is left out for now
(the user, 2026-11-08: more investigation later; speedDIAL 2.0's API is
documented only to its partners).
"""
from __future__ import annotations

import re

# the makes FCP Euro and ECS Tuning sell for (their own make menus)
_FCP = ["Audi", "BMW", "Mercedes-Benz", "Mini", "Porsche", "Volkswagen", "Volvo"]
_ECS = ["Audi", "BMW", "Mercedes-Benz", "Mini", "Porsche", "Volkswagen"]

DEFAULTS = [
    {"name": "FCP Euro", "url": "https://www.fcpeuro.com/Parts?keywords={pn}", "makes": _FCP},
    {"name": "ECS Tuning", "url": "https://www.ecstuning.com/Search/SiteSearch/{pn}/", "makes": _ECS},
    {"name": "RockAuto", "url": "https://www.rockauto.com/en/partsearch/?partnum={pn}", "makes": []},
]

MAX_SUPPLIERS = 20
MAX_MAKES = 40


def make_key(make: str) -> str:
    """"Mercedes-Benz", "Mercedes Benz", "mercedes benz" -> "mercedesbenz"
    (the same as index.html's makeKey)."""
    return re.sub(r"[^a-z0-9]", "", str(make or "").lower())


def fits(supplier: dict, make: str) -> bool:
    """A supplier with no makes is for every car. Otherwise the car's make
    must start with one of its makes, so "Mercedes-Benz" also covers
    WIS's "Mercedes-Benz Vans". A car with no make gets only the
    every-car suppliers."""
    keys = [make_key(m) for m in supplier.get("makes") or [] if make_key(m)]
    if not keys:
        return True
    car = make_key(make)
    return bool(car) and any(car.startswith(k) for k in keys)


def current(cfg) -> list[dict]:
    """The shop's list, or the defaults while it has never saved one."""
    saved = getattr(cfg, "suppliers", None)
    if isinstance(saved, list):
        try:
            return clean(saved)
        except ValueError:     # a hand-edited config.json: fall back, never fail
            pass
    return [dict(s, makes=list(s["makes"])) for s in DEFAULTS]


def clean(items: list) -> list[dict]:
    """A list from Shop setup, checked: raises ValueError with what to fix.
    The address must be a web address (http or https -- never
    javascript: or a file) with exactly one {pn}; the browser checks the
    scheme again before it makes a link."""
    if not isinstance(items, list):
        raise ValueError("The supplier list isn't a list.")
    if len(items) > MAX_SUPPLIERS:
        raise ValueError(f"At most {MAX_SUPPLIERS} suppliers.")
    out, seen = [], set()
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Supplier {n} isn't readable.")
        name = " ".join(str(item.get("name") or "").split())
        url = str(item.get("url") or "").strip()
        makes = item.get("makes") or []
        if isinstance(makes, str):
            makes = makes.split(",")
        if not isinstance(makes, list):
            raise ValueError(f"{name or f'Supplier {n}'}: the makes aren't a list.")
        makes = [" ".join(str(m).split()) for m in makes]
        makes = list(dict.fromkeys(m for m in makes if m))
        label = name or f"Supplier {n}"
        if not name:
            raise ValueError(f"Supplier {n} needs a name.")
        if len(name) > 40:
            raise ValueError(f"{label}: the name is longer than 40 characters.")
        if name.lower() in seen:
            raise ValueError(f"{label} is in the list twice.")
        seen.add(name.lower())
        if len(url) > 400 or not re.fullmatch(r"https?://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?", url):
            raise ValueError(f"{label}: the search address must be a web address "
                             "starting with https:// (or http://).")
        if url.count("{pn}") != 1:
            raise ValueError(f"{label}: put {{pn}} once in the search address, "
                             "where the part number goes.")
        if len(makes) > MAX_MAKES or any(len(m) > 40 for m in makes):
            raise ValueError(f"{label}: too many makes, or one too long.")
        out.append({"name": name, "url": url, "makes": makes})
    return out
