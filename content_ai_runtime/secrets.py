from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .paths import SECRETS_FALLBACK_PATH, ensure_app_dir


SERVICE = "shortform-ai"
LEGACY_SERVICE = "content-ai"
INSTAGRAM_ACCOUNT = "instagram_sessionid"


class SecretStoreError(RuntimeError):
    pass


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _macos_read(account: str, *, service: str = SERVICE) -> str | None:
    proc = _run_security(["find-generic-password", "-w", "-s", service, "-a", account])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _macos_write(account: str, value: str, *, service: str = SERVICE) -> None:
    proc = _run_security([
        "add-generic-password",
        "-U",
        "-s",
        service,
        "-a",
        account,
        "-w",
        value,
    ])
    if proc.returncode != 0:
        raise SecretStoreError(proc.stderr.strip() or "macOS Keychain write failed")


def _macos_delete(account: str, *, service: str = SERVICE) -> None:
    _run_security(["delete-generic-password", "-s", service, "-a", account])


def _fallback_data() -> dict[str, str]:
    if not SECRETS_FALLBACK_PATH.exists():
        return {}
    try:
        return json.loads(SECRETS_FALLBACK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _fallback_read(account: str) -> str | None:
    value = _fallback_data().get(account)
    return value if isinstance(value, str) and value else None


def _fallback_write(account: str, value: str) -> None:
    ensure_app_dir()
    data = _fallback_data()
    data[account] = value
    SECRETS_FALLBACK_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        SECRETS_FALLBACK_PATH.chmod(0o600)
    except OSError:
        pass


def _fallback_delete(account: str) -> None:
    if not SECRETS_FALLBACK_PATH.exists():
        return
    data = _fallback_data()
    data.pop(account, None)
    if data:
        _fallback_write_map(data)
    else:
        try:
            SECRETS_FALLBACK_PATH.unlink()
        except OSError:
            pass


def _fallback_write_map(data: dict[str, str]) -> None:
    ensure_app_dir()
    SECRETS_FALLBACK_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        SECRETS_FALLBACK_PATH.chmod(0o600)
    except OSError:
        pass


def backend_name() -> str:
    if sys.platform == "darwin":
        return "macos-keychain"
    if sys.platform == "win32":
        return "fallback-file"
    return "fallback-file"


def read_instagram_session() -> str | None:
    env_value = os.getenv("SHORTFORM_AI_INSTAGRAM_SESSIONID") or os.getenv("CONTENT_AI_INSTAGRAM_SESSIONID")
    if env_value:
        return env_value.strip()
    if sys.platform == "darwin":
        return (
            _macos_read(INSTAGRAM_ACCOUNT)
            or _macos_read(INSTAGRAM_ACCOUNT, service=LEGACY_SERVICE)
            or _fallback_read(INSTAGRAM_ACCOUNT)
        )
    return _fallback_read(INSTAGRAM_ACCOUNT)


def write_instagram_session(sessionid: str, *, allow_fallback: bool = False) -> str:
    if sys.platform == "darwin":
        try:
            _macos_write(INSTAGRAM_ACCOUNT, sessionid)
            return "macos-keychain"
        except SecretStoreError:
            if not allow_fallback:
                raise
    elif not allow_fallback:
        raise SecretStoreError(
            "Secure credential storage is not implemented for this platform. "
            "Re-run with --allow-insecure-file-storage to store locally in the shortform-ai config directory."
        )

    _fallback_write(INSTAGRAM_ACCOUNT, sessionid)
    return "fallback-file"


def delete_instagram_session() -> None:
    if sys.platform == "darwin":
        _macos_delete(INSTAGRAM_ACCOUNT)
        _macos_delete(INSTAGRAM_ACCOUNT, service=LEGACY_SERVICE)
    _fallback_delete(INSTAGRAM_ACCOUNT)
