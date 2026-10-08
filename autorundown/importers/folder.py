"""Generic fallback importer: a plain folder of documents.

Handles HTML, text, and (when `pypdf` is installed) PDF. Metadata is inferred
from the directory path and filename, which is inherently best-effort -- this
importer exists so an unrecognized archive still indexes rather than failing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..extract import element_to_text, parse_file
from .base import Importer, PageRecord, VehicleRecord, register

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
MAKES = (
    "acura alfa audi bmw buick cadillac chevrolet chrysler dodge ferrari fiat ford genesis "
    "gmc honda hyundai infiniti isuzu jaguar jeep kia land-rover lexus lincoln mazda "
    "mclaren mercedes mercury mini mitsubishi nissan oldsmobile plymouth polestar pontiac "
    "porsche ram rivian saab saturn scion subaru suzuki tesla toyota volkswagen volvo"
).split()
TEXT_SUFFIXES = {".html", ".htm", ".txt", ".md"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _read_pdf(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        return [(pg.extract_text() or "") for pg in PdfReader(str(path)).pages]
    except Exception:
        return []


@register
class FolderImporter(Importer):
    name = "folder"

    @classmethod
    def detect(cls, root: Path) -> bool:
        return root.is_dir()

    def _docs(self, root: Path) -> list[Path]:
        return sorted(
            p for p in root.rglob("*")
            if p.is_file() and (p.suffix.lower() in TEXT_SUFFIXES or p.suffix.lower() == ".pdf")
        )

    def discover(self, root: Path) -> Iterator[VehicleRecord]:
        files = self._docs(root)
        if not files:
            return
        haystack = root.name.lower()
        year = YEAR_RE.search(root.name)
        make = next((m for m in MAKES if m in haystack), "")
        yield VehicleRecord(
            doc_id=_slug(root.name) or "archive",
            source_name="folder",
            file_path=str(root),
            make=make.title(),
            model=root.name,
            year_range=year.group(1) if year else "",
            doc_type="manual",
            page_count=len(files),
        )

    def pages(self, vehicle: VehicleRecord) -> Iterator[PageRecord]:
        root = Path(vehicle.file_path)
        for path in self._docs(root):
            rel = path.relative_to(root)
            section_path = [p for p in rel.parts[:-1]]
            suffix = path.suffix.lower()

            if suffix == ".pdf":
                for n, page_text in enumerate(_read_pdf(path), start=1):
                    if page_text.strip():
                        yield PageRecord(
                            doc_id=vehicle.doc_id,
                            page_no=f"{_slug(str(rel))}-p{n}",
                            file_path=str(path),
                            title=f"{path.stem} (page {n})",
                            section_path=section_path,
                            system=section_path[0] if section_path else "",
                            text=page_text.strip(),
                        )
                continue

            if suffix in (".html", ".htm"):
                tree = parse_file(path)
                if tree is None:
                    continue
                body = tree.xpath("//body") or [tree]
                text, images, links = element_to_text(body[0])
            else:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                images = []
                links = []

            if not text:
                continue
            yield PageRecord(
                doc_id=vehicle.doc_id,
                page_no=_slug(str(rel)),
                file_path=str(path),
                title=path.stem,
                section_path=section_path,
                system=section_path[0] if section_path else "",
                text=text,
                images=images,
                links=links,
            )
