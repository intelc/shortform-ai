from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote
import importlib.util


@dataclass(frozen=True)
class InstagramSession:
    user_id: str
    sessionid: str
    chrome_profile: str
    cookie_file: str


def _chrome_user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "Google" / "Chrome" / "User Data"
        return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    return Path("/nonexistent")


def _chrome_cookie_files() -> list[str]:
    base = _chrome_user_data_dir()
    if not base.is_dir():
        return []
    out: list[str] = []
    for pattern in (
        base / "Default" / "Cookies",
        base / "Default" / "Network" / "Cookies",
        base / "Profile *" / "Cookies",
        base / "Profile *" / "Network" / "Cookies",
    ):
        out.extend(glob.glob(str(pattern)))
    return out


def _session_user_id(sessionid: str) -> str | None:
    user_id = sessionid.split(":", 1)[0] if ":" in sessionid else ""
    return user_id if user_id.isdigit() else None


def _normalize_sessionid(sessionid: str) -> str:
    candidates = [sessionid]
    decoded = unquote(sessionid)
    if decoded != sessionid:
        candidates.append(decoded)
    for candidate in list(candidates):
        if len(candidate) > 32:
            candidates.append(candidate[32:])
    for candidate in candidates:
        if _session_user_id(candidate):
            return candidate
    return sessionid


def _chrome_profile_name(cookie_file: str) -> str:
    path = Path(cookie_file)
    parts = path.parts
    if "Network" in parts and len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return "Chrome"


def _copy_cookie_db(path: str) -> tuple[str, str] | None:
    try:
        src = Path(path)
        temp_dir = tempfile.mkdtemp(prefix="shortform-ai-cookies-")
        temp_path = Path(temp_dir) / src.name
        shutil.copy2(src, temp_path)
        for suffix in ("-wal", "-shm"):
            sibling = Path(f"{src}{suffix}")
            if sibling.exists():
                shutil.copy2(sibling, Path(f"{temp_path}{suffix}"))
        return temp_dir, str(temp_path)
    except OSError:
        return None


def _get_chrome_safe_storage_key() -> bytes | None:
    try:
        pw = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            stderr=subprocess.DEVNULL,
            timeout=15,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    if not pw:
        return None
    import hashlib

    return hashlib.pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, dklen=16)


def _decrypt_macos_cookie(blob: bytes, key: bytes) -> str | None:
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None

    if blob[:3] not in (b"v10", b"v11"):
        return None

    body = blob[3:]
    if len(body) > 16 and (len(body) - 16) % 16 == 0:
        try:
            iv = body[:16]
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            plain = cipher.decryptor().update(body[16:]) + cipher.decryptor().finalize()
            pad = plain[-1]
            if 1 <= pad <= 16:
                plain = plain[:-pad]
                if len(plain) >= 16:
                    return plain[16:].decode("utf-8", errors="replace")
        except Exception:
            pass

    try:
        iv = b" " * 16
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        plain = cipher.decryptor().update(body) + cipher.decryptor().finalize()
        pad = plain[-1]
        if 1 <= pad <= 16:
            return plain[:-pad].decode("utf-8", errors="replace")
    except Exception:
        return None
    return None


def _windows_decrypt_dpapi(blob: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
        raw = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_char))), raw

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _ = to_blob(blob)
    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _get_windows_chrome_master_key() -> bytes | None:
    local_state = _chrome_user_data_dir() / "Local State"
    if not local_state.is_file():
        return None
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
        wrapped = base64.b64decode(data["os_crypt"]["encrypted_key"])
    except Exception:
        return None
    if wrapped.startswith(b"DPAPI"):
        wrapped = wrapped[5:]
    return _windows_decrypt_dpapi(wrapped)


def _decrypt_windows_cookie(blob: bytes, key: bytes) -> str | None:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    if blob.startswith((b"v10", b"v11")) and len(blob) > 31:
        nonce = blob[3:15]
        payload = blob[15:]
        try:
            return AESGCM(key).decrypt(nonce, payload, None).decode("utf-8", errors="replace")
        except Exception:
            pass
    decrypted = _windows_decrypt_dpapi(blob)
    if decrypted:
        return decrypted.decode("utf-8", errors="replace")
    return None


def read_instagram_sessions_from_files(
    *,
    files: list[str],
    decrypt_value: Callable[[bytes], str | None],
    use_temp_copy: bool = True,
) -> tuple[list[InstagramSession], str | None]:
    sessions: list[InstagramSession] = []
    seen: set[tuple[str, str]] = set()

    for cookie_file in files:
        db_path = cookie_file
        temp_dir: str | None = None
        if use_temp_copy:
            staged = _copy_cookie_db(cookie_file)
            if staged is None:
                continue
            temp_dir, db_path = staged

        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
            rows = con.execute(
                "SELECT host_key, value, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%instagram.com' AND name = 'sessionid'"
            ).fetchall()
            con.close()
            for _host, value, encrypted in rows:
                sessionid = value or ""
                if not sessionid and encrypted:
                    sessionid = decrypt_value(encrypted) or ""
                if not sessionid:
                    continue
                sessionid = _normalize_sessionid(sessionid)
                user_id = _session_user_id(sessionid)
                if not user_id:
                    continue
                key = (user_id, sessionid)
                if key in seen:
                    continue
                seen.add(key)
                sessions.append(
                    InstagramSession(
                        user_id=user_id,
                        sessionid=sessionid,
                        chrome_profile=_chrome_profile_name(cookie_file),
                        cookie_file=cookie_file,
                    )
                )
        except Exception:
            continue
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    if sessions:
        return sessions, None
    return [], "Found Chrome profile(s) but no Instagram sessionid cookie. Are you logged in to instagram.com?"


def list_instagram_sessions() -> tuple[list[InstagramSession], str | None]:
    files = _chrome_cookie_files()
    if not files:
        if sys.platform == "win32":
            return [], "No Chrome profile found under %LOCALAPPDATA%\\Google\\Chrome\\User Data"
        if sys.platform == "darwin":
            return [], "No Chrome profile found at ~/Library/Application Support/Google/Chrome"
        return [], "Chrome cookie extraction is only supported on macOS and Windows in v1"

    if sys.platform == "darwin":
        if importlib.util.find_spec("cryptography") is None:
            return [], "Chrome cookie decryption requires Python package `cryptography`. Run `shortform-ai setup` or set SHORTFORM_AI_PYTHON to an environment that has cryptography installed."
        key = _get_chrome_safe_storage_key()
        if key is None:
            return [], "Could not read 'Chrome Safe Storage' from macOS Keychain"
        return read_instagram_sessions_from_files(
            files=files,
            decrypt_value=lambda blob: _decrypt_macos_cookie(blob, key),
            use_temp_copy=True,
        )

    if sys.platform == "win32":
        key = _get_windows_chrome_master_key()
        if key is None:
            return [], "Could not read Chrome's encrypted cookie key from Local State via Windows DPAPI"
        return read_instagram_sessions_from_files(
            files=files,
            decrypt_value=lambda blob: _decrypt_windows_cookie(blob, key),
            use_temp_copy=True,
        )

    return [], "Chrome cookie extraction is only supported on macOS and Windows in v1"


def select_sessionid(instagram_user_id: str = "") -> tuple[str | None, str | None, list[InstagramSession]]:
    sessions, err = list_instagram_sessions()
    if err:
        return None, err, sessions
    if not sessions:
        return None, "No Instagram sessions found in Chrome", sessions
    selected = instagram_user_id.strip()
    if selected:
        for session in sessions:
            if session.user_id == selected or session.chrome_profile == selected:
                return session.sessionid, None, sessions
        return None, f"Configured Instagram account/profile {selected} was not found in Chrome.", sessions
    if len(sessions) == 1:
        return sessions[0].sessionid, None, sessions
    return None, "Multiple Instagram accounts were found in Chrome.", sessions
