"""Configuration: archive path, index path, API key.

Key resolution order (spec 1.7): explicit config file -> OS keychain -> env var.
A key is only ever sent to its own AI provider (providers.py). Which key pays
for a given request (the company's, or a mechanic's own, for the provider in
use) is decided in one place: `pick_key`.
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


def make_private_dir(path: Path) -> None:
    """mkdir -p, the last folder private (0700) when it is created on
    Linux/macOS: it holds the API key, the index with every question asked,
    and auth.db. Windows is left as before (the user profile is already
    private, and a mode there means ACLs since Python 3.13)."""
    if os.name == "nt":
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)


@dataclass
class Config:
    archive_dir: str = ""
    index_path: str = str(DEFAULT_INDEX)
    importer: str = "auto"
    # the company's Anthropic key (the key of before providers existed)
    api_key: str = ""
    model: str = "claude-sonnet-5"
    # which AI answers: "anthropic" | "gemini" | "xai" | "deepseek" (providers.py)
    provider: str = "anthropic"
    # the company keys of the other providers: {"gemini": "AIza...", ...}.
    # Entered in Shop setup, never fetched; never sent to a phone.
    provider_keys: dict = field(default_factory=dict)
    # models left out of the question box's model menu (Shop setup). The menu
    # lists every model of every provider that has a key; `provider` + `model`
    # are its default, which is never hidden.
    hidden_models: list = field(default_factory=list)
    # a provider's newer models the shop added from its model list (Shop
    # setup, providers.list_models): {"anthropic": ["claude-..."], ...}.
    # Price not known: their questions are logged "cost not tracked".
    extra_models: dict = field(default_factory=dict)
    # the Mercedes WIS extractor (vm_pipeline.py lives there); "" = this
    # repo's extractor/. Its data goes outside the repo: see extractor/wis_paths.py
    extractor_dir: str = ""
    # the Mercedes VM's VirtualBox machine file (.vbox), picked by the user
    # in Shop setup's Mercedes VM tab (the user, 2026-10-23: a file to choose,
    # not a list filled in for them); "" = none chosen yet
    vm_file: str = ""
    # other devices must sign in with a mechanic account (auth.py); off =
    # anyone on the network can search and ask. The PC itself never signs in.
    require_signin: bool = False
    # WIS damage-code picture menus (search.show_damage_codes): for warranty
    # fault codes, not repairs; off = left out of search, Ask and the Manual
    # contents (the user, 2026-10-24: off by default, a setting to show them)
    show_damage_codes: bool = False
    # where the LEMON torrent is (or will be) downloaded; its lemon/index.json
    # replaces the built-in catalogue snapshot (lemon_catalogue.py)
    lemon_torrent_dir: str = ""
    # whose API key pays for an answer (pick_key): "company" = `api_key` for
    # everyone; "per_mechanic" = each signed-in mechanic's own key from
    # `mechanic_keys`, the PC itself still on `api_key`
    key_mode: str = "company"
    # per_mechanic mode: {"<auth.db user id>": {"api_key": "sk-ant-...",
    #   "loaded_usd": 20.0 | None,   # credit the owner says is on the key
    #   "since": "YYYY-MM-DD HH:MM:SS",  # UTC; spend counts from here
    #   "providers": {"gemini": {"api_key", "loaded_usd", "since"}, ...}}}
    # The top level is the Anthropic key; other providers' keys sit under
    # "providers" (mechanic_entry). Never sent to a phone, never logged; the
    # admin sees the last 4 only.
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
        make_private_dir(APP_DIR)
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


def active_provider(cfg: Config) -> str:
    from .providers import PROVIDERS, DEFAULT_PROVIDER
    return cfg.provider if cfg.provider in PROVIDERS else DEFAULT_PROVIDER


def active_model(cfg: Config) -> str:
    """The saved model when it is one of the active provider's, else that
    provider's default (a model saved under another provider)."""
    from .providers import PROVIDERS
    p = PROVIDERS[active_provider(cfg)]
    return cfg.model if cfg.model in p["models"] else p["default_model"]


def menu_models(cfg: Config, user_id: int | None, admin: bool) -> list[str]:
    """The question box's model menu for this requester: every model of
    every provider they have a key for (pick_key: the company's, or their
    own in per-mechanic mode), less the hidden ones; the default first.
    Empty when they have no key at all."""
    from .providers import PROVIDERS
    out = []
    for pid, p in PROVIDERS.items():
        try:
            if not pick_key(cfg, user_id, admin, pid).key:
                continue
        except NoKeyForRequest:
            continue
        out += [m for m in p["models"] if m not in (cfg.hidden_models or [])]
    default = active_model(cfg)
    if default in out:          # absent when the default provider has no key for them
        out.remove(default)
        out.insert(0, default)
    return out


def company_key(cfg: Config, provider: str = "anthropic") -> str:
    """The company key for a provider: config file first, then environment
    (ANTHROPIC_API_KEY, GEMINI_API_KEY, XAI_API_KEY, DEEPSEEK_API_KEY)."""
    from .providers import PROVIDERS
    if provider == "anthropic":
        return cfg.resolved_key
    saved = (cfg.provider_keys or {}).get(provider) or ""
    env = PROVIDERS.get(provider, {}).get("env_key", "")
    return saved or (os.environ.get(env, "") if env else "")


def key_ref(provider: str, user_id: int | None = None) -> str:
    """usage_log.key_ref: "company" / "user:<id>" for Anthropic (as before
    providers existed, so old rows still count), with ":<provider>" after it
    for the others -- a mechanic's Gemini key is counted apart from his
    Anthropic key."""
    base = "company" if user_id is None else f"user:{user_id}"
    return base if provider == "anthropic" else f"{base}:{provider}"


def mechanic_entry(cfg: Config, user_id: int | str, provider: str = "anthropic") -> dict:
    """A mechanic's key entry for one provider: {api_key, loaded_usd, since}."""
    entry = cfg.mechanic_keys.get(str(user_id)) or {}
    if provider == "anthropic":
        return {k: v for k, v in entry.items() if k != "providers"}
    return dict((entry.get("providers") or {}).get(provider) or {})


def set_mechanic_entry(cfg: Config, user_id: int | str, provider: str,
                       new: dict | None) -> None:
    """Store (or with None, remove) a mechanic's key for one provider,
    leaving their keys for the other providers alone."""
    uid = str(user_id)
    entry = dict(cfg.mechanic_keys.get(uid) or {})
    others = dict(entry.get("providers") or {})
    if provider == "anthropic":
        entry = dict(new or {})
    else:
        entry = {k: v for k, v in entry.items() if k != "providers"}
        if new:
            others[provider] = dict(new)
        else:
            others.pop(provider, None)
    if others:
        entry["providers"] = others
    if entry:
        cfg.mechanic_keys[uid] = entry
    else:
        cfg.mechanic_keys.pop(uid, None)


def pick_key(cfg: Config, user_id: int | None, admin: bool,
             provider: str | None = None) -> KeyChoice:
    """THE place that decides which API key pays for a request.

    For the provider in use (default: the active one; a key is only ever for
    its own provider). company mode: the company key for everyone (empty ->
    query.NoApiKey as before). per_mechanic mode: the PC (admin) uses the
    company key; a signed-in mechanic uses their own key for this provider,
    and with none set is refused -- it never falls back to the company key,
    another mechanic's, or their own key for another provider; a device that
    is not signed in is refused too (it has no key of its own)."""
    from .providers import PROVIDERS
    provider = provider or active_provider(cfg)
    label = PROVIDERS.get(provider, {}).get("short", provider)
    if cfg.key_mode != "per_mechanic" or admin:
        return KeyChoice(company_key(cfg, provider), key_ref(provider))
    if user_id is None:
        raise NoKeyForRequest(
            "AI answers in this shop use each mechanic's own API key. Sign in "
            "(ask for a sign-in QR at the shop PC) to ask; keyword search works without it.")
    entry = mechanic_entry(cfg, user_id, provider)
    if not entry.get("api_key"):
        raise NoKeyForRequest(
            ("No API key" if provider == "anthropic" else f"No {label} API key")
            + " is set up for you yet. Ask the shop to add your key in "
            "Shop setup > AI answers; keyword search still works.")
    return KeyChoice(entry["api_key"], key_ref(provider, user_id))


def key_last4(key: str) -> str:
    return key[-4:] if len(key) >= 12 else ""


def load() -> Config:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}
        cfg = Config(**{k: v for k, v in raw.items() if k in known})
        if not isinstance(cfg.mechanic_keys, dict):   # hand-edited file
            cfg.mechanic_keys = {}
        if not isinstance(cfg.provider_keys, dict):
            cfg.provider_keys = {}
        if not isinstance(cfg.hidden_models, list):
            cfg.hidden_models = []
        if not isinstance(cfg.extra_models, dict):
            cfg.extra_models = {}
    else:
        cfg = Config()
    from .providers import sync_added
    sync_added(cfg.extra_models)       # the added models join the providers' tables
    return cfg
