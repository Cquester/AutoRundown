"""Generate a small stand-in LEMON archive, for trying the LEMON import and
the reading view without the real manuals.

    python make_sample_lemon.py [--out sample_lemon]
    python ingest.py --archive sample_lemon --importer lemon --index sample.db

The text and numbers are invented -- **this is NOT manual content; never use
a value from it on a car.** What is faithful is the layout the importer reads
(see autorundown/importers/lemon.py): one folder per vehicle with index.html,
pages/<n>.html carrying the breadcrumb (Home >> Make >> Year >> Variant >>
section ...) and a <div class="main">, and diagrams under images/. Free: no
API calls, no network.
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (folder, make, year, variant)
VEHICLES = [
    ("2015-ford-f-150", "Ford", "2015", "F-150 2WD V6-3.5L Turbo"),
    ("1987-mercedes-190e", "Mercedes Benz", "1987", "190E L4-2.3L (102.985)"),
    ("2008-toyota-camry", "Toyota", "2008", "Camry L4-2.4L (2AZ-FE)"),
]

# (section path, title, body html) -- invented sample text
PAGES = [
    (["Engine, Cooling and Exhaust", "Engine", "Specifications"], "Engine Oil Capacity",
     "<p>SAMPLE DATA -- not from a manual.</p>"
     "<table><tr><th>Item</th><th>Value</th></tr>"
     "<tr><td>Engine oil with filter</td><td>5.7 L (6.0 qt)</td></tr>"
     "<tr><td>Drain plug torque</td><td>27 Nm (20 ft. lbs.)</td></tr></table>"),
    (["Brakes and Traction Control", "Disc Brake System", "Service and Repair"], "Front Brake Pads - Removal and Installation",
     "<p>SAMPLE DATA -- not from a manual.</p><ol>"
     "<li>Raise and support the vehicle. Remove the front wheel.</li>"
     "<li>Remove the caliper guide pin bolts and lift the caliper off.</li>"
     "<li>Remove the pads. Compress the piston.</li>"
     "<li>Install new pads. Tighten the guide pin bolts to 33 Nm (24 ft. lbs.).</li></ol>"
     '<img src="../images/brake-caliper.svg">'),
    (["Engine, Cooling and Exhaust", "Engine", "Cylinder Head Assembly"], "Valve Cover - Removal and Installation",
     "<p>SAMPLE DATA -- not from a manual.</p><ol>"
     "<li>Disconnect the negative battery cable.</li>"
     "<li>Remove the ignition coils and the valve cover bolts.</li>"
     "<li>Install a new gasket. Tighten the bolts in sequence to 10 Nm (89 inch lbs.).</li></ol>"
     '<p>Refer to: <a href="2.html">Engine Oil Capacity</a></p>'),
    (["Starting and Charging", "Charging System", "Specifications"], "Generator Specifications",
     "<p>SAMPLE DATA -- not from a manual.</p>"
     "<table><tr><th>Item</th><th>Value</th></tr>"
     "<tr><td>Output</td><td>150 A</td></tr>"
     "<tr><td>Mounting bolts</td><td>47 Nm (35 ft. lbs.)</td></tr></table>"),
]

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="160" viewBox="0 0 320 160">'
       '<rect width="320" height="160" fill="#fff"/>'
       '<rect x="40" y="40" width="120" height="80" rx="12" fill="none" stroke="#333" stroke-width="4"/>'
       '<circle cx="230" cy="80" r="40" fill="none" stroke="#333" stroke-width="4"/>'
       '<text x="160" y="150" font-family="sans-serif" font-size="14" text-anchor="middle">'
       'SAMPLE diagram</text></svg>')


def _page(crumbs: list[str], title: str, body: str) -> str:
    bc = " &gt;&gt; ".join(f'<a class="breadcrumb-part" href="#">{html.escape(c)}</a>' for c in crumbs)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head>"
            f"<body><div class='breadcrumbs'>{bc}</div><h1>{html.escape(title)}</h1>"
            f"<div class='main'>{body}</div></body></html>")


def make(out: Path) -> Path:
    for folder, make_, year, variant in VEHICLES:
        vdir = out / folder
        (vdir / "pages").mkdir(parents=True, exist_ok=True)
        (vdir / "images").mkdir(exist_ok=True)
        (vdir / "images" / "brake-caliper.svg").write_text(SVG, encoding="utf-8")
        head = ["Home", make_, year, variant]
        (vdir / "index.html").write_text(
            f"<!doctype html><html><body><h1>{html.escape(year)} {html.escape(make_)} "
            f"{html.escape(variant)}</h1><a href='pages/1.html'>Repair and Diagnosis</a></body></html>",
            encoding="utf-8")
        # pages/1.html names the vehicle (the importer reads its breadcrumb)
        nav = "".join(f'<li><a href="{n}.html">{html.escape(t)}</a></li>'
                      for n, (_, t, _b) in enumerate(PAGES, start=2))
        (vdir / "pages" / "1.html").write_text(
            _page(head, "Repair and Diagnosis", f"<ul>{nav}</ul>"), encoding="utf-8")
        for n, (section, title, body) in enumerate(PAGES, start=2):
            (vdir / "pages" / f"{n}.html").write_text(
                _page(head + section, title, body), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "sample_lemon"))
    args = ap.parse_args()
    out = make(Path(args.out))
    print(f"wrote {len(VEHICLES)} sample LEMON vehicle(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
