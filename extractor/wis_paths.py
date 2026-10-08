"""Where the extractor's data lives: never inside the repo.

The extractor runs from the AutoRundown repo (`<repo>/extractor`), but what it
reads and writes is large and not ours to publish: the EWA install (bundled
Java, tbjdbc.jar) and `autorundown_out/` (pulls, corpus, 11 GB of images).
Those stay in the "Mercedes Documents" folder:

  AR_OUT_ROOT  the autorundown_out folder itself, if set
  AR_HOME      the folder holding autorundown_out/ and the EWA install, if set
  otherwise    the nearest "Mercedes Documents" folder beside the repo or one
               of its parents (Desktop/Mercedes Documents for the usual layout,
               also found from a worktree), or this folder's parent when the
               extractor still sits in that folder (the old standalone layout)

An output root inside the repo is refused: one run would put gigabytes of
Mercedes data where `git add` can reach it.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent if (HERE.parent / ".git").exists() else None


def home() -> Path:
    """The folder holding autorundown_out/ and the EWA install."""
    if os.environ.get("AR_HOME"):
        return Path(os.environ["AR_HOME"]).resolve()
    if REPO is None:
        return HERE.parent                      # .../Mercedes Documents/autorundown_extractor
    for p in REPO.parents:
        if (p / "Mercedes Documents").is_dir():
            return p / "Mercedes Documents"
    return REPO.parent / "Mercedes Documents"   # created on first use, outside the repo


def out_root() -> Path:
    """The autorundown_out folder."""
    root = Path(os.environ.get("AR_OUT_ROOT") or home() / "autorundown_out").resolve()
    check_outside_repo(root)
    return root


def check_outside_repo(path: Path) -> None:
    if REPO is not None and Path(path).resolve().is_relative_to(REPO.resolve()):
        raise SystemExit(f"refusing to write extractor data inside the repo: {path}\n"
                         f"set AR_OUT_ROOT (or AR_HOME) to a folder outside {REPO}")


if __name__ == "__main__":
    print(f"home     {home()}\nout_root {out_root()}")
