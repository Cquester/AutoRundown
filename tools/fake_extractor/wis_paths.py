"""Where the fake extractor (tools/fake_extractor/vm_pipeline.py) writes: a
temporary folder, never the user's autorundown_out. Deliberately not
AR_OUT_ROOT -- that may point at the real corpus in the shell this runs from."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def out_root() -> Path:
    return Path(os.environ.get("AR_FAKE_VM_OUT")
                or Path(tempfile.gettempdir()) / "ar_fake_vm_out").resolve()
