"""Puts the repo root (ingest.py, the autorundown package) and tools/ (the
sample-data makers) on sys.path, so a test runs as `python tests/<name>.py`
from anywhere. Import it before anything from the repo."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
