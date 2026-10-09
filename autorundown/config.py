"""Configuration: archive path, index path, API key.

Key resolution order (spec 1.7): explicit config file -> OS keychain -> env var.
The key is only ever sent to Anthropic.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

APP_DIR = Path(os.environ.get("AUTORUNDOWN_HOME", Path.home() / ".autorundown"))
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_INDEX = APP_DIR / "index.db"
_SAVE_LOCK = threading.Lock()


@dataclass
class Config:
    archive_dir: str = ""
    index_path: str = str(DEFAULT_INDEX)
    importer: str = "auto"
    api_key: str = ""
    model: str = "claude-opus-5"
    # the Mercedes WIS extractor (vm_pipeline.py lives there); "" = this
    # repo's extractor/. Its data goes outside the repo: see extractor/wis_paths.py
    extractor_dir: str = ""
    # other devices must sign in with a mechanic account (auth.py); off =
    # anyone on the network can search and ask. The PC itself never signs in.
    require_signin: bool = False
    # where the LEMON torrent is (or will be) downloaded; its lemon/index.json
    # replaces the built-in catalogue snapshot (lemon_catalogue.py)
    lemon_torrent_dir: str = ""

    @property
    def resolved_key(self) -> str:
        """Config file first, then environment. Never logged, never persisted elsewhere."""
        return self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def save(self) -> None:
        """Atomically: every request reads this file, and one read while it
        was half-written failed with a JSON error (111 of 400 requests made
        during repeated saves). Written whole to a private temp file, then
        swapped in."""
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = json.dumps(asdict(self), indent=2).encode("utf-8")
        with _SAVE_LOCK:
            tmp = CONFIG_PATH.with_name(f".config.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            for attempt in range(20):
                try:
                    os.replace(tmp, CONFIG_PATH)
                    break
                except PermissionError:   # Windows: a reader has it open for a moment
                    if attempt == 19:
                        tmp.unlink(missing_ok=True)
                        raise
                    time.sleep(0.02)


def load() -> Config:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}
        return Config(**{k: v for k, v in raw.items() if k in known})
    return Config()
