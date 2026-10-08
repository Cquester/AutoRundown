"""Importer for LEMON-style HTML manual archives.

Layout (per vehicle):
    <vehicle dir>/index.html
    <vehicle dir>/pages/<n>.html      one page per procedure / table / diagram
    <vehicle dir>/images/<name>       referenced diagrams

Vehicle identity comes from the breadcrumb on pages/1.html
(Home >> Make >> Year >> Variant), which is more reliable than parsing the
directory name -- it keeps multi-word makes like "Mercedes Benz" intact.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..extract import element_to_text, parse_file
from .base import Importer, PageRecord, VehicleRecord, register

ENGINE_RE = re.compile(r"((?:[LVIHW]\d+-)?\d+\.\d+\s*L\b[^,]*)", re.I)
DRIVE_RE = re.compile(r"\b(4WD|2WD|AWD|FWD|RWD|4X4|4X2)\b", re.I)
SINGLE_PAGE = "Repair and Diagnosis (Single Page)"

WIRING_HINTS = ("wiring diagram", "connector view", "component location", "harness")
TSB_HINTS = ("technical service bulletin", "service bulletin", "recall", "campaign")
SPEC_HINTS = ("specification", "capacit", "fluid", "torque", "quick lookup", "labor time")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _breadcrumbs(tree) -> list[str]:
    return [
        (a.text_content() or "").strip()
        for a in tree.xpath("//a[contains(@class,'breadcrumb-part')]")
    ]


def _classify(section_path: list[str]) -> str:
    joined = " ".join(section_path).lower()
    if any(h in joined for h in WIRING_HINTS):
        return "wiring"
    if any(h in joined for h in TSB_HINTS):
        return "tsb"
    if any(h in joined for h in SPEC_HINTS):
        return "spec_table"
    return "manual"


VARIANT_WARNING = "different variant/trim"


def _applicability(section_path: list[str], text: str) -> str:
    """Flag pages that belong to a sibling trim or engine.

    This archive files other-variant material under the selected vehicle and
    stamps it with an in-page warning. Both signals are checked, since either
    alone would let a spec for the wrong engine answer as if it fitted.
    """
    haystack = (" ".join(section_path) + " " + text[:400]).lower()
    return "other_variant" if VARIANT_WARNING in haystack else "this_vehicle"


def _is_navigation(main) -> bool:
    """Link-tree pages (the folder listings) carry no content worth indexing.

    They are almost entirely anchor text, so compare the characters inside
    links against the page total rather than hardcoding page numbers.
    """
    links = main.xpath(".//a")
    if len(links) < 20:
        return False
    link_chars = sum(len((a.text_content() or "").strip()) for a in links)
    total = len(main.text_content() or "")
    return total > 0 and link_chars / total > 0.6


@register
class LemonImporter(Importer):
    name = "lemon"

    @classmethod
    def _vehicle_dirs(cls, root: Path, max_depth: int = 3) -> list[Path]:
        found: list[Path] = []

        def scan(d: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = list(d.iterdir())
            except (PermissionError, OSError):
                return
            if (d / "pages" / "1.html").exists() and (d / "index.html").exists():
                found.append(d)
                return  # don't descend into a vehicle dir
            for e in entries:
                if e.is_dir():
                    scan(e, depth + 1)

        scan(root, 0)
        return sorted(found)

    @classmethod
    def detect(cls, root: Path) -> bool:
        return bool(cls._vehicle_dirs(root, max_depth=3))

    def discover(self, root: Path) -> Iterator[VehicleRecord]:
        for vdir in self._vehicle_dirs(root):
            tree = parse_file(vdir / "pages" / "1.html")
            crumbs = _breadcrumbs(tree) if tree is not None else []
            # Home >> Make >> Year >> Variant
            make = crumbs[1] if len(crumbs) > 1 else ""
            year = crumbs[2] if len(crumbs) > 2 else ""
            variant = crumbs[3] if len(crumbs) > 3 else vdir.name

            engine_m = ENGINE_RE.search(variant)
            drive_m = DRIVE_RE.search(variant)
            pages_dir = vdir / "pages"
            count = sum(1 for _ in pages_dir.glob("*.html"))

            yield VehicleRecord(
                doc_id=_slug(f"{year}-{make}-{variant}") or _slug(vdir.name),
                source_name="lemon",
                file_path=str(vdir),
                make=make,
                model=variant,
                year_range=year,
                engine=engine_m.group(1).strip() if engine_m else "",
                drivetrain=drive_m.group(1).upper() if drive_m else "",
                doc_type="manual",
                page_count=count,
            )

    def pages(self, vehicle: VehicleRecord) -> Iterator[PageRecord]:
        pages_dir = Path(vehicle.file_path) / "pages"
        files = sorted(
            pages_dir.glob("*.html"),
            key=lambda p: int(p.stem) if p.stem.isdigit() else 1 << 30,
        )
        for path in files:
            try:
                tree = parse_file(path)
            except Exception:
                continue
            if tree is None:
                continue

            crumbs = _breadcrumbs(tree)
            section_path = [c for c in crumbs[4:] if c and c != SINGLE_PAGE]

            main = tree.xpath("//div[@class='main']")
            if not main:
                continue
            if _is_navigation(main[0]):
                continue  # link tree, not content
            text, images, links = element_to_text(main[0])
            if not text:
                continue

            h1 = tree.xpath("//h1")
            title = (h1[0].text_content() or "").strip() if h1 else (
                section_path[-1] if section_path else path.stem
            )

            yield PageRecord(
                doc_id=vehicle.doc_id,
                page_no=path.stem,
                file_path=str(path),
                title=title,
                section_path=section_path,
                system=section_path[0] if section_path else "",
                doc_type=_classify(section_path),
                text=text,
                images=images,
                links=links,
                applicability=_applicability(section_path, text),
            )
