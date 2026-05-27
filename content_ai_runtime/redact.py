from __future__ import annotations


SECRET_MARKER = "[REDACTED]"


def redact_secret(value: str | None, *, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return SECRET_MARKER
    return f"{value[:keep]}…{SECRET_MARKER}"


def contains_secret(text: str, secret: str | None) -> bool:
    return bool(secret and secret in text)
