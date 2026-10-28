"""Unpack a LEMON car's downloaded zip into <app dir>/manuals/<doc_id>/ for
the import that follows (the "Add this car" job; autorundown/lemon_zip.py
says what is checked).

    python unzip_lemon.py --zip "LEMON 2015 Ford F-150 ....zip" --dest <app dir>/manuals/<doc_id>

Exit 0 unpacked, 2 refused (the zip is not one safe LEMON car), 1 other.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autorundown import lemon_zip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args()
    z, dest = Path(a.zip), Path(a.dest)
    print(f"== Unpacking {z.name}", flush=True)
    try:
        n = lemon_zip.unpack(z, dest, lambda d, t: print(f"unzipped {d}/{t} files", flush=True))
    except lemon_zip.BadZip as e:
        print(f"STOP: {e}", flush=True)
        return 2
    print(f"unpacked {n:,} files to {dest}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
