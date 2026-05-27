from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PRIMARY_APP_DIR = Path.home() / ".shortform-ai"
LEGACY_APP_DIR = Path.home() / ".content-ai"
APP_DIR = Path(
    os.getenv("SHORTFORM_AI_HOME")
    or os.getenv("CONTENT_AI_HOME")
    or (LEGACY_APP_DIR if LEGACY_APP_DIR.exists() and not PRIMARY_APP_DIR.exists() else PRIMARY_APP_DIR)
)
CONFIG_PATH = APP_DIR / "config.json"
SECRETS_FALLBACK_PATH = APP_DIR / "secrets.json"
RUNTIME_DIR = APP_DIR / "runtime"


def ensure_app_dir() -> Path:
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return APP_DIR


def ensure_runtime_dir() -> Path:
    ensure_app_dir()
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return RUNTIME_DIR


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(config: dict[str, Any]) -> None:
    ensure_app_dir()
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
