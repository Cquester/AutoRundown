"""Configuration: archive path, index path, API key.

Key resolution order (spec 1.7): explicit config file -> OS keychain -> env var.
The key is only ever sent to Anthropic. Which key pays for a given request
(the company's, or a mechanic's own) is decided in one place: `pick_key`.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict, field
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
    # whose API key pays for an answer (pick_key): "company" = `api_key` for
    # everyone; "per_mechanic" = each signed-in mechanic's own key from
    # `mechanic_keys`, the PC itself still on `api_key`
    key_mode: str = "company"
    # per_mechanic mode: {"<auth.db user id>": {"api_key": "sk-ant-...",
    #   "loaded_usd": 20.0 | None,   # credit the owner says is on the key
    #   "since": "YYYY-MM-DD HH:MM:SS"}}  # UTC; spend counts from here
    # Never sent to a phone, never logged; the admin sees the last 4 only.
    mechanic_keys: dict = field(default_factory=dict)

    @property
    def resolved_key(self) -> str:
        """The company key: config file first, then environment. Never logged,
        never persisted elsewhere."""
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


KEY_MODES = ("company", "per_mechanic")


class NoKeyForRequest(RuntimeError):
    """This request has no key it may use. Said plainly to the person asking;
    never answered by quietly using somebody else's key."""


@dataclass
class KeyChoice:
    key: str
    # tags the spend in usage_log.key_ref: "company" or "user:<id>"; the
    # per-key "spent since loaded" counts rows with this tag. Never the key.
    ref: str


def pick_key(cfg: Config, user_id: int | None, admin: bool) -> KeyChoice:
    """THE place that decides which API key pays for a request.

    company mode: the company key for everyone (empty -> query.NoApiKey as
    before). per_mechanic mode: the PC (admin) uses the company key; a
    signed-in mechanic uses their own key, and with none set is refused -- it
    never falls back to the company key or another mechanic's; a device that
    is not signed in is refused too (it has no key of its own)."""
    if cfg.key_mode != "per_mechanic" or admin:
        return KeyChoice(cfg.resolved_key, "company")
    if user_id is None:
        raise NoKeyForRequest(
            "AI answers in this shop use each mechanic's own API key. Sign in "
            "(ask for a sign-in QR at the shop PC) to ask; keyword search works without it.")
    entry = cfg.mechanic_keys.get(str(user_id)) or {}
    if not entry.get("api_key"):
        raise NoKeyForRequest(
            "No API key is set up for you yet. Ask the shop to add your key in "
            "Shop setup > AI answers; keyword search still works.")
    return KeyChoice(entry["api_key"], f"user:{user_id}")


def key_last4(key: str) -> str:
    return key[-4:] if len(key) >= 12 else ""


def load() -> Config:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}
        cfg = Config(**{k: v for k, v in raw.items() if k in known})
        if not isinstance(cfg.mechanic_keys, dict):   # hand-edited file
            cfg.mechanic_keys = {}
        return cfg
    return Config()
