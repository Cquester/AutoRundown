"""Configuration: archive path, index path, API key.

Key resolution order (spec 1.7): explicit config file -> OS keychain -> env var.
The key is only ever sent to Anthropic.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

APP_DIR = Path(os.environ.get("AUTORUNDOWN_HOME", Path.home() / ".autorundown"))
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_INDEX = APP_DIR / "index.db"


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

    @property
    def resolved_key(self) -> str:
        """Config file first, then environment. Never logged, never persisted elsewhere."""
        return self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass  # best effort on Windows


def load() -> Config:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}
        return Config(**{k: v for k, v in raw.items() if k in known})
    return Config()
