"""Where the extractor's data lives: never inside the repo.

The extractor runs from the AutoRundown repo (`<repo>/extractor`), but what it
reads and writes is large and not ours to publish: the EWA install (bundled
Java, tbjdbc.jar) and `autorundown_out/` (pulls, corpus, 11 GB of images).
Those stay in the "Mercedes Documents" folder:

  AR_OUT_ROOT  the autorundown_out folder itself, if set
  AR_HOME      the folder holding autorundown_out/ and the EWA install, if set
  otherwise    the nearest "Mercedes Documents" folder beside the repo or one
               of its parents (Desktop/Mercedes Documents for the usual layout,
               also found from a worktree); in the installed app (the MSI's
               app/extractor) the app's own data folder,
               %LOCALAPPDATA%/AutoRundown/Mercedes data (installed_home); or
               this folder's parent when the extractor still sits in that
               folder (the old standalone layout)

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
    if REPO is None and (HERE.parent / "autorundown").is_dir():
        # the MSI's app/extractor: never write into the app. An older
        # install's Desktop folder stays in use until the server has moved
        # it (a failed move strands nothing)
        new, old = installed_home(), legacy_home()
        return old if old is not None and not new.exists() else new
    if REPO is None:
        return HERE.parent                      # .../Mercedes Documents/autorundown_extractor
    for p in REPO.parents:
        if (p / "Mercedes Documents").is_dir():
            return p / "Mercedes Documents"
    return REPO.parent / "Mercedes Documents"   # created on first use, outside the repo


HOME_NAME = "Mercedes data"


def installed_home() -> Path:
    """The installed app's Mercedes folder: beside the program, never on the
    Desktop (the user, 2026-10-31: a 64 GB folder showing their own name,
    and OneDrive syncs a Desktop). %LOCALAPPDATA%/AutoRundown/Mercedes data;
    under AUTORUNDOWN_HOME when that is set (tests, a second instance).
    Uninstalling leaves it; Shop setup's "Delete all Mercedes data" removes
    it. The server moves an older install's Desktop folder here
    (importer_jobs.move_mercedes_home, legacy_home)."""
    if os.environ.get("AUTORUNDOWN_HOME"):
        return Path(os.environ["AUTORUNDOWN_HOME"]) / HOME_NAME
    base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(base) / "AutoRundown" / HOME_NAME   # created on first use


def legacy_home() -> Path | None:
    """Where the installed app kept it until 0.1.10: Desktop/Mercedes
    Documents (the OneDrive Desktop's too), if it is there."""
    for desk in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        if (desk / "Mercedes Documents").is_dir():
            return desk / "Mercedes Documents"
    return None


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
