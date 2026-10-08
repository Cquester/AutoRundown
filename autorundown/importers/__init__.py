"""Importer registry. Import side effects register each importer by name."""
from .base import Importer, PageRecord, VehicleRecord, autodetect, available, get, register
from . import lemon, records, folder  # noqa: F401  (registration side effects)

__all__ = [
    "Importer", "PageRecord", "VehicleRecord",
    "autodetect", "available", "get", "register",
]
