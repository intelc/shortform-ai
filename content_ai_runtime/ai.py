from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


API_KEY_ENV = ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")
OPENAI_MODEL_ENV = "SHORTFORM_AI_OPENAI_MODEL"
LEGACY_OPENAI_MODEL_ENV = "CONTENT_AI_OPENAI_MODEL"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
PLACEHOLDER_KEYS = {"", "your-openai-api-key"}

ANALYSIS_SCHEMA = {
    "name": "content_ai_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["visual_summary", "strategy_summary", "on_screen_text", "timeline", "ideas"],
        "properties": {
            "visual_summary": {"type": "string"},
            "strategy_summary": {"type": "string"},
            "on_screen_text": {
                "type": "array",
                "items": {"type": "string"},
            },
            "timeline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "beat_number",
                        "start_time",
                        "end_time",
                        "visual_evidence",
                        "transcript_excerpt",
                        "inferred_purpose",
                    ],
                    "properties": {
                        "beat_number": {"type": "integer"},
                        "start_time": {"type": "number"},
                        "end_time": {"type": "number"},
                        "visual_evidence": {"type": "string"},
                        "transcript_excerpt": {"type": "string"},
                        "inferred_purpose": {"type": "string"},
                    },
                },
            },
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "description", "reasoning"],
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class AIAvailability:
    codex_path: str | None
    api_keys: tuple[str, ...]


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _unquote_env_value(value)


def load_env_files(start: Path | None = None) -> None:
    """Load simple KEY=value entries from nearby .env files without printing secrets."""
    if os.getenv("SHORTFORM_AI_DISABLE_DOTENV") == "1" or os.getenv("CONTENT_AI_DISABLE_DOTENV") == "1":
        return
    start_dir = (start or Path.cwd()).resolve()
    if start_dir.is_file():
        start_dir = start_dir.parent
    seen: set[Path] = set()
    for directory in (start_dir, *start_dir.parents):
        env_path = directory / ".env"
        if env_path not in seen:
            _load_env_file(env_path)
            seen.add(env_path)
        if directory == Path.home():
            break


def _usable_env_key(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return value not in PLACEHOLDER_KEYS


def detect_ai() -> AIAvailability:
    load_env_files()
    return AIAvailability(
        codex_path=shutil.which("codex"),
        api_keys=tuple(name for name in API_KEY_ENV if _usable_env_key(name)),
    )


def resolve_ai_mode(requested: str) -> tuple[str | None, str | None]:
    requested = requested.strip().lower()
    available = detect_ai()
    if requested == "agent":
        return "agent", None
    if requested == "codex":
        return ("codex", None) if available.codex_path else (None, "Codex CLI not found. Run `codex login` after installing Codex, or use --ai api.")
    if requested == "api":
        return ("api", None) if available.api_keys else (None, "No provider API key found. Set OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY.")
    if requested != "auto":
        return None, "Invalid --ai value. Use auto, agent, codex, or api."
    return "agent", None


def codex_command(analysis_dir: Path, images: list[Path], prompt: str) -> list[str]:
    cmd = [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-C",
        str(analysis_dir),
    ]
    for image in images:
        cmd.extend(["--image", str(image)])
    cmd.append("-")
    return cmd


def _extract_last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found = value
            if {"visual_summary", "strategy_summary", "ideas"}.issubset(value.keys()):
                expected = value
    if expected is not None:
        return expected
    if found is None:
        raise RuntimeError("Codex CLI returned no JSON object")
    return found


def run_codex_json(
    *,
    analysis_dir: Path,
    images: list[Path],
    prompt: str,
    timeout: int = 180,
) -> dict[str, Any]:
    proc = subprocess.run(
        codex_command(analysis_dir, images, prompt),
        capture_output=True,
        text=True,
        input=prompt,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError("Codex CLI analysis failed")
    return _extract_last_json_object(proc.stdout.strip())


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def run_openai_vision_json(
    *,
    images: list[Path],
    prompt: str,
    timeout: int = 90,
) -> dict[str, Any]:
    load_env_files()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key in PLACEHOLDER_KEYS:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        detail = "high" if image.name == "contact_sheet.jpg" else "low"
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(image), "detail": detail},
        })
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.getenv(OPENAI_MODEL_ENV) or os.getenv(LEGACY_OPENAI_MODEL_ENV, DEFAULT_OPENAI_MODEL),
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert short-form video analyst. Return concise, concrete JSON only.",
                },
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_schema", "json_schema": ANALYSIS_SCHEMA},
            "max_tokens": 1200,
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API returned HTTP {response.status_code}")
    data = response.json()
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text:
        raise RuntimeError("OpenAI API returned empty content")
    return json.loads(text)
