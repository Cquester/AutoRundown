"""Puts the repo root (ingest.py, the autorundown package) and tools/ (the
sample-data makers) on sys.path, so a test runs as `python tests/<name>.py`
from anywhere. Import it before anything from the repo."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def exit_now(rc: int) -> None:
    """End a browser test with its result, skipping Python's shutdown: on the
    macOS runner, test_phone_ui printed "all passed" and then crashed with a
    segmentation fault while exiting (2026-10-31), failing a passing run."""
    import os
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc or 0)
