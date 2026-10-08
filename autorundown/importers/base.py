"""Importer interface and the normalized internal format.

Everything downstream of this module (indexing, search, query, UI) reads only
VehicleRecord / PageRecord. Adding support for a new archive layout means
writing one Importer subclass -- nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


@dataclass
class VehicleRecord:
    """One vehicle's manual set -- the 'document' of the spec's manifest."""
    doc_id: str
    source_name: str          # which importer/archive this came from
    file_path: str            # root directory of this vehicle's manual
    make: str = ""
    model: str = ""
    year_range: str = ""
    engine: str = ""
    drivetrain: str = ""
    doc_type: str = "manual"  # manual | tsb | wiring | spec_table
    page_count: int = 0
    # Documents in the same source that state no model, and so apply to every
    # model in it. Searched alongside this vehicle; see search.scope_ids.
    general_doc_id: str = ""
    # Manufacturer model-series code (Mercedes Baumuster series, "204"). Used
    # to resolve a VIN to a vehicle; empty where a source has no such code.
    model_code: str = ""
    # Source vehicle-type codes this vehicle's own documents carry, as
    # ",0,2," (WIS fzg_art bits). Narrows which general documents are in
    # scope; empty means unknown, and nothing is narrowed.
    vehicle_types: str = ""
    # The specific models this vehicle's documents cover, for the picker:
    # [{"name": "190 E 2.3 16", "baumuster": ["201.034"]}, ...]. Choosing one
    # selects *this* vehicle -- documents are filed at this level, not per
    # variant. Empty: the picker shows the vehicle itself.
    variants: list = field(default_factory=list)
    # US VIN codes resolving to this vehicle, as ",WDB:D," (WMI + the letter
    # at VIN position 4). European FINs need none: they carry the Baumuster.
    vin_codes: str = ""

    @property
    def label(self) -> str:
        bits = [self.year_range, self.make, self.model]
        return " ".join(b for b in bits if b) or self.doc_id


@dataclass
class PageRecord:
    """One retrievable page, with its position in the manual's hierarchy."""
    doc_id: str
    page_no: str              # stable id within the vehicle (filename stem here)
    file_path: str
    title: str = ""
    section_path: list[str] = field(default_factory=list)
    system: str = ""          # top-level system, e.g. "Brakes"
    doc_type: str = "manual"
    text: str = ""            # normalized plain text, tables rendered as pipe tables
    images: list[str] = field(default_factory=list)
    # "pageno::label" cross-references the source page carried
    links: list[str] = field(default_factory=list)
    # "this_vehicle" or "other_variant" -- archives often carry pages for
    # sibling trims/engines. Never let those answer as if they applied here.
    applicability: str = "this_vehicle"
    # Source vehicle-type codes the page is flagged for, as ",0,2," -- see
    # VehicleRecord.vehicle_types. Empty means unrestricted.
    vehicle_types: str = ""
    # Engine families the page is limited to, as ",272,642," -- a page filed
    # under a car can still be for one of its engines only ("ENGINE 642 in
    # MODEL 212"). Empty means any engine.
    engines: str = ""
    # Exact model / engine codes the page names as its only targets (title,
    # "Engine 102.942 ...") or excludes ("except 102.983/99"), as ",102.942,".
    # The 190E 2.3-16 (102.983) was answered from a page "Engine 102 (except
    # 102.983/99)" before search could read these.
    codes_only: str = ""
    codes_except: str = ""

    @property
    def page_uid(self) -> str:
        return f"{self.doc_id}:{self.page_no}"


class Importer:
    """Given a directory, emit normalized vehicles and their pages."""

    name: str = "base"

    def __init__(self, option: str = "") -> None:
        # Set from the part after ':' in an importer name ("records:model"),
        # for importers that read one archive more than one way.
        self.option = option

    @classmethod
    def detect(cls, root: Path) -> bool:
        """True if this importer recognizes the layout under `root`."""
        raise NotImplementedError

    def discover(self, root: Path) -> Iterable[VehicleRecord]:
        raise NotImplementedError

    def pages(self, vehicle: VehicleRecord) -> Iterator[PageRecord]:
        raise NotImplementedError


_REGISTRY: dict[str, type[Importer]] = {}


def register(cls: type[Importer]) -> type[Importer]:
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Importer:
    """Look up an importer, optionally with a mode: "records:model"."""
    base, _, option = name.partition(":")
    if base not in _REGISTRY:
        raise KeyError(f"unknown importer {base!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[base](option)


def available() -> list[str]:
    return sorted(_REGISTRY)


def autodetect(root: Path) -> Importer:
    """Pick the most specific importer that recognizes this archive."""
    for name, cls in sorted(_REGISTRY.items()):
        if name == "folder":
            continue  # generic fallback goes last
        if cls.detect(root):
            return cls()
    return get("folder")
