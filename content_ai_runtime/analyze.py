from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from . import __version__
from .ai import ANALYSIS_SCHEMA, PLACEHOLDER_KEYS, load_env_files, resolve_ai_mode, run_codex_json, run_openai_vision_json
from .instagram_client import (
    WebInstagramClient,
    WebInstagramClientError,
    extract_instagram_creator_metadata,
    extract_instagram_audio_metadata,
    extract_instagram_metrics,
    extract_shortcode_from_url,
    shortcode_to_media_id,
)
from .media import (
    acquire_video,
    create_contact_sheet,
    detect_shots,
    extract_audio,
    extract_keyframes,
    ffprobe_duration,
    has_audio_stream,
    is_url,
)
from .secrets import read_instagram_session

import requests

TranscribeMode = Literal["auto", "api", "local", "none"]
DEFAULT_LOCAL_TRANSCRIBE_MODEL = "base"
LOCAL_TRANSCRIBE_INSTALL = "pip install --upgrade 'shortform-ai[local-transcribe]'"
LOCAL_TRANSCRIBE_PIPX_INSTALL = "pipx inject --force shortform-ai 'shortform-ai[local-transcribe]'"


REQUIRED_ARTIFACTS = [
    "manifest.json",
    "reel.json",
    "media.json",
    "transcript.md",
    "transcript.srt",
    "onscreen_text.md",
    "shots.json",
    "visual.md",
    "report.md",
    "comments.json",
    "audience.md",
    "strategy.md",
    "ideas.json",
]

AGENT_ARTIFACTS = [
    "agent_request.md",
    "agent_schema.json",
    "agent_response.example.json",
]


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _noop_progress(_message: str) -> None:
    return None


def _empty_srt() -> str:
    return "1\n00:00:00,000 --> 00:00:00,000\n\n"


def _format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _segments_to_srt(segments: list[dict]) -> str:
    if not segments:
        return _empty_srt()
    blocks = []
    for index, segment in enumerate(segments, 1):
        start = _format_srt_time(float(segment.get("start") or 0.0))
        end = _format_srt_time(float(segment.get("end") or segment.get("start") or 0.0))
        text = str(segment.get("text") or "").strip()
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _has_openai_key() -> bool:
    load_env_files()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return api_key not in PLACEHOLDER_KEYS


def _transcription_required_message(mode: TranscribeMode, local_model: str, detail: str | None = None) -> str:
    prefix = "Transcription is required before short-form video analysis can continue."
    if detail:
        prefix = f"{prefix} {detail}"
    return (
        f"{prefix}\n"
        "Choose one setup path, then rerun the same analyze command:\n"
        "1. OpenAI transcription: set OPENAI_API_KEY in your shell or a nearby .env file, "
        "then run `shortform-ai doctor` to confirm it is detected.\n"
        "2. Local transcription now: run `shortform-ai setup transcription --local`, "
        f"or install manually with `{LOCAL_TRANSCRIBE_PIPX_INSTALL}` for pipx installs "
        f"or `{LOCAL_TRANSCRIBE_INSTALL}` inside a venv, then rerun. "
        f"The default local model is `{local_model}` and downloads on first use.\n"
        "If you are working through Codex, tell Codex which path you chose; after setup it should rerun "
        "`shortform-ai doctor` and continue."
    )


def _transcribe_with_openai(video_path: Path) -> tuple[str, list[dict]]:
    load_env_files()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key in PLACEHOLDER_KEYS or not has_audio_stream(video_path):
        return "", []

    with tempfile.TemporaryDirectory(prefix="shortform-ai-audio-") as tmp:
        audio_path = Path(tmp) / "audio.mp3"
        if not extract_audio(video_path, audio_path):
            return "", []
        with audio_path.open("rb") as audio_file:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": "whisper-1",
                    "response_format": "verbose_json",
                    "language": "en",
                },
                files={"file": ("audio.mp3", audio_file, "audio/mpeg")},
                timeout=180,
            )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI transcription returned HTTP {response.status_code}")
    data = response.json()
    segments = [
        {
            "start": float(segment.get("start") or 0.0),
            "end": float(segment.get("end") or 0.0),
            "text": str(segment.get("text") or "").strip(),
        }
        for segment in data.get("segments", [])
        if str(segment.get("text") or "").strip()
    ]
    return str(data.get("text") or "").strip(), segments


def _transcribe_with_faster_whisper(video_path: Path, model_name: str) -> tuple[str, list[dict]]:
    if not has_audio_stream(video_path):
        return "", []
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local transcription requires `faster-whisper`. "
            "Run `shortform-ai setup transcription --local` or install `shortform-ai[local-transcribe]`."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="shortform-ai-local-audio-") as tmp:
        audio_path = Path(tmp) / "audio.mp3"
        if not extract_audio(video_path, audio_path):
            return "", []
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        raw_segments, _info = model.transcribe(str(audio_path), beam_size=5)
        segments = [
            {
                "start": float(segment.start or 0.0),
                "end": float(segment.end or 0.0),
                "text": str(segment.text or "").strip(),
            }
            for segment in raw_segments
            if str(segment.text or "").strip()
        ]
    return " ".join(segment["text"] for segment in segments).strip(), segments


def _run_transcription(
    video_path: Path,
    mode: TranscribeMode,
    local_model: str,
) -> tuple[str, list[dict], str | None, str | None]:
    if mode == "none":
        return "", [], None, _transcription_required_message(
            mode,
            local_model,
            "`--transcribe none` disables a required step.",
        )

    if mode == "api" or (mode == "auto" and _has_openai_key()):
        if mode == "api" and not _has_openai_key():
            return "", [], None, _transcription_required_message(
                mode,
                local_model,
                "OPENAI_API_KEY is not configured.",
            )
        try:
            text, segments = _transcribe_with_openai(video_path)
        except Exception as exc:
            if mode == "api":
                return "", [], None, _transcription_required_message(
                    mode,
                    local_model,
                    f"OpenAI transcription failed ({exc}).",
                )
        else:
            return text, segments, "api" if text else None, None

    if mode in {"auto", "local"}:
        try:
            text, segments = _transcribe_with_faster_whisper(video_path, local_model)
        except Exception as exc:
            if mode == "local":
                return "", [], None, _transcription_required_message(mode, local_model, str(exc))
            return "", [], None, None
        warning = (
            f"Using bundled local transcription inference with faster-whisper model `{local_model}`. "
            "For cloud transcription, set OPENAI_API_KEY and rerun with `--transcribe api`."
        )
        return text, segments, "local" if text else None, warning

    return "", [], None, _transcription_required_message(mode, local_model)


def _analysis_prompt(shots: list[dict]) -> str:
    return (
        "Analyze the attached keyframes for a short-form video. Return only JSON with "
        "keys: visual_summary (string), strategy_summary (string), on_screen_text "
        "(array of strings, every readable visible caption/text overlay in sequence "
        "from the contact sheet and keyframes; preserve wording as exactly as possible), "
        "ideas (array of objects with title, description, reasoning). Keep it concrete "
        "and grounded. Treat on-screen text as separate from audio transcription. "
        f"There are {len(shots)} detected shots."
    )


def _agent_response_example() -> dict:
    return {
        "visual_summary": "Describe the visual story, pacing, editing, and what the contact sheet/keyframes show.",
        "strategy_summary": "Explain why the reel works and what a creator should learn from it.",
        "on_screen_text": [
            "Transcribe visible captions/text overlays in order.",
        ],
        "ideas": [
            {
                "title": "A concrete remix idea",
                "description": "What to make.",
                "reasoning": "Why it follows from the evidence.",
            }
        ],
    }


def _write_agent_handoff(
    out_dir: Path,
    *,
    source: str,
    shots: list[dict],
    contact_sheet: Path | None,
    keyframes: list[Path],
) -> None:
    image_lines = []
    if contact_sheet:
        image_lines.append(f"- `contact_sheet.jpg`")
    image_lines.extend(f"- `{_artifact_link_path(path, out_dir)}`" for path in keyframes[:6])
    image_block = "\n".join(image_lines) if image_lines else "- No images were generated."
    prompt = _analysis_prompt(shots)
    request = f"""# Shortform AI Agent Request

You are the active LLM agent in this interface. Do not call `codex exec`,
`claude`, or any nested agent CLI. Inspect the local artifacts in this analysis
directory and write the response yourself.

Source: {source}

## Read First

- `report.md`
- `manifest.json`
- `media.json`
- `reel.json`
- `transcript.md`
- `onscreen_text.md`
- `shots.json`
- `audience.md`
- `comments.json`

## Visual Evidence

{image_block}

If `contact_sheet.jpg` exists, inspect it directly and include it inline in your
final user-facing summary. Treat `onscreen_text.md` as provisional; improve it
from the contact sheet/keyframes when you can read visible captions.

## Response Contract

Write JSON matching `agent_schema.json` to `agent_response.json`, then run:

```bash
shortform-ai agent apply {out_dir}
```

The JSON keys are:

- `visual_summary`: concise grounded visual analysis.
- `strategy_summary`: why the short-form piece works and what to learn.
- `on_screen_text`: ordered visible caption/text overlay strings.
- `ideas`: objects with `title`, `description`, and `reasoning`.

## Analysis Prompt

{prompt}
"""
    _write_text(out_dir / "agent_request.md", request)
    _write_json(out_dir / "agent_schema.json", ANALYSIS_SCHEMA["schema"])
    _write_json(out_dir / "agent_response.example.json", _agent_response_example())


def _validate_agent_response(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("agent response must be a JSON object")
    required = ("visual_summary", "strategy_summary", "on_screen_text", "ideas")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"agent response missing required key(s): {', '.join(missing)}")
    visual_summary = str(payload.get("visual_summary") or "").strip()
    strategy_summary = str(payload.get("strategy_summary") or "").strip()
    if not visual_summary:
        raise ValueError("agent response visual_summary must be non-empty")
    if not strategy_summary:
        raise ValueError("agent response strategy_summary must be non-empty")
    raw_text = payload.get("on_screen_text")
    if not isinstance(raw_text, list):
        raise ValueError("agent response on_screen_text must be a list")
    on_screen_text = [str(line).strip() for line in raw_text if str(line).strip()]
    raw_ideas = payload.get("ideas")
    if not isinstance(raw_ideas, list):
        raise ValueError("agent response ideas must be a list")
    ideas = []
    for item in raw_ideas:
        if not isinstance(item, dict):
            continue
        ideas.append({
            "title": str(item.get("title") or "Untitled idea").strip(),
            "description": str(item.get("description") or "").strip(),
            "reasoning": str(item.get("reasoning") or "").strip(),
        })
    return {
        "visual_summary": visual_summary,
        "strategy_summary": strategy_summary,
        "on_screen_text": on_screen_text,
        "ideas": ideas,
    }


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _strip_markdown_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def apply_agent_response(analysis_dir: Path, response_path: Path | None = None) -> tuple[int, str]:
    analysis_dir = analysis_dir.expanduser().resolve()
    response_path = (response_path or analysis_dir / "agent_response.json").expanduser().resolve()
    if not response_path.exists():
        return 1, f"agent response not found: {response_path}"

    try:
        response = _validate_agent_response(json.loads(response_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return 1, f"invalid agent response: {exc}"

    manifest = _read_json(analysis_dir / "manifest.json", {})
    if not isinstance(manifest, dict):
        manifest = {}
    reel = _read_json(analysis_dir / "reel.json", {})
    if not isinstance(reel, dict):
        reel = {}
    media = _read_json(analysis_dir / "media.json", {})
    if not isinstance(media, dict):
        media = {}
    shots_payload = _read_json(analysis_dir / "shots.json", {"shots": []})
    shots = shots_payload.get("shots", []) if isinstance(shots_payload, dict) else []
    if not isinstance(shots, list):
        shots = []

    source = str(manifest.get("source") or reel.get("source") or media.get("source") or "unknown")
    duration = reel.get("duration") or media.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    instagram = media.get("instagram") or reel.get("instagram") or {"status": "not_instagram_url"}
    if not isinstance(instagram, dict):
        instagram = {"status": "not_instagram_url"}

    transcript_text = _strip_markdown_heading((analysis_dir / "transcript.md").read_text(encoding="utf-8")) if (analysis_dir / "transcript.md").exists() else ""
    audience_text = _strip_markdown_heading((analysis_dir / "audience.md").read_text(encoding="utf-8")) if (analysis_dir / "audience.md").exists() else "No comments fetched for this analysis."
    contact_sheet = analysis_dir / "contact_sheet.jpg"
    contact_sheet_path = contact_sheet if contact_sheet.exists() else None

    _write_text(analysis_dir / "visual.md", f"# Visual Analysis\n\n{response['visual_summary']}")
    _write_text(analysis_dir / "strategy.md", f"# Strategy\n\n{response['strategy_summary']}")
    _write_text(analysis_dir / "onscreen_text.md", f"# On-Screen Text\n\n{_format_on_screen_text(response['on_screen_text'])}")
    _write_json(analysis_dir / "ideas.json", {"ideas": response["ideas"]})
    _write_text(
        analysis_dir / "report.md",
        _render_report(
            source=source,
            out_dir=analysis_dir,
            status="complete",
            duration=duration,
            shots=shots,
            instagram=instagram,
            transcript_text=transcript_text,
            on_screen_text=response["on_screen_text"],
            visual_summary=response["visual_summary"],
            strategy_summary=response["strategy_summary"],
            ideas=response["ideas"],
            contact_sheet=contact_sheet_path,
            comments_note=audience_text or "No comments fetched for this analysis.",
        ),
    )

    manifest["status"] = "complete"
    manifest["ai_mode"] = "agent"
    manifest["warning"] = None
    manifest["agent_response"] = str(response_path)
    capabilities = manifest.setdefault("capabilities", {})
    if isinstance(capabilities, dict):
        capabilities["agent_synthesis"] = True
    _write_json(analysis_dir / "manifest.json", manifest)
    return 0, f"agent response applied to {analysis_dir}"


def _fetch_instagram_metadata(source: str) -> tuple[dict, bool]:
    shortcode = extract_shortcode_from_url(source)
    if not shortcode:
        return {"status": "not_instagram_url"}, False

    media_id = shortcode_to_media_id(shortcode)
    if not media_id:
        return {"status": "invalid_shortcode", "shortcode": shortcode}, False

    metadata = {
        "status": "not_configured",
        "shortcode": shortcode,
        "media_id": str(media_id),
        "audio": extract_instagram_audio_metadata({}),
        "creator": {},
        "metrics": {},
    }
    session = read_instagram_session()
    if not session:
        return metadata, False

    try:
        client = WebInstagramClient(sessionid=session)
        item = client.get_media_info(str(media_id))
    except (WebInstagramClientError, OSError, RuntimeError) as exc:
        metadata["status"] = "error"
        metadata["error"] = str(exc)
        return metadata, True

    metadata["status"] = "fetched"
    metadata["audio"] = extract_instagram_audio_metadata(item)
    metadata["creator"] = extract_instagram_creator_metadata(item)
    metadata["metrics"] = extract_instagram_metrics(item)
    username = metadata["creator"].get("username")
    if username:
        try:
            profile = client.get_user_profile(str(username))
        except (WebInstagramClientError, OSError, RuntimeError) as exc:
            metadata["creator"]["profile_status"] = "error"
            metadata["creator"]["profile_error"] = str(exc)
        else:
            metadata["creator"] = {**metadata["creator"], **profile, "profile_status": "fetched"}
    return metadata, True


def _build_reel_payload(source: str, video_path: Path, duration: float | None, shots: list[dict], instagram: dict) -> dict:
    reel = {
        "source": source,
        "local_video": str(video_path),
        "duration": duration,
        "shot_count": len(shots),
    }
    if instagram.get("status") != "not_instagram_url":
        reel["instagram"] = instagram
        reel["instagram_audio"] = instagram.get("audio") or extract_instagram_audio_metadata({})
        reel["instagram_creator"] = instagram.get("creator") or {}
        reel["instagram_metrics"] = instagram.get("metrics") or {}
    return reel


def _build_media_payload(source: str, video_path: Path, duration: float | None, instagram: dict) -> dict:
    return {
        "source": source,
        "local_video": str(video_path),
        "duration": duration,
        "instagram": instagram,
    }


def _audio_line(instagram: dict) -> str:
    audio = instagram.get("audio") or {}
    title = audio.get("title")
    artist = audio.get("artist")
    audio_type = audio.get("audio_type") or "unknown"
    if title and artist:
        return f"{title} by {artist} ({audio_type})"
    if title:
        return f"{title} ({audio_type})"
    return f"Unknown ({audio_type})"


def _format_count(value: object) -> str | None:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return None


def _has_present_value(data: dict) -> bool:
    return any(value not in (None, "", [], {}) for value in data.values())


def _creator_line(instagram: dict) -> str:
    creator = instagram.get("creator") or {}
    username = creator.get("username")
    if not username:
        return "Unavailable"
    parts = [f"@{username}"]
    full_name = creator.get("full_name")
    if full_name:
        parts.append(f"({full_name})")
    follower_count = _format_count(creator.get("follower_count"))
    if follower_count:
        parts.append(f"- {follower_count} followers")
    media_count = _format_count(creator.get("media_count"))
    if media_count:
        parts.append(f"- {media_count} posts")
    if creator.get("is_verified") is True:
        parts.append("- verified")
    return " ".join(parts)


def _metrics_line(instagram: dict) -> str:
    metrics = instagram.get("metrics") or {}
    labels = [
        ("Plays", metrics.get("play_count")),
        ("Views", metrics.get("view_count")),
        ("Likes", metrics.get("like_count")),
        ("Comments", metrics.get("comment_count")),
        ("Shares", metrics.get("reshare_count")),
        ("Saves", metrics.get("save_count")),
    ]
    rendered = [f"{label}: {formatted}" for label, value in labels if (formatted := _format_count(value))]
    if metrics.get("taken_at_iso"):
        rendered.append(f"Posted: {metrics['taken_at_iso']}")
    return "; ".join(rendered) if rendered else "Unavailable"


def _format_on_screen_text(lines: list[str]) -> str:
    cleaned = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not cleaned:
        return "No on-screen text extracted."
    return "\n".join(f"- {line}" for line in cleaned)


def _artifact_link_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _render_report(
    *,
    source: str,
    out_dir: Path,
    status: str,
    duration: float | None,
    shots: list[dict],
    instagram: dict,
    transcript_text: str,
    on_screen_text: list[str],
    visual_summary: str,
    strategy_summary: str,
    ideas: list,
    contact_sheet: Path | None,
    comments_note: str,
) -> str:
    lines = [
        "# Shortform AI Report",
        "",
        f"Source: {source}",
        f"Status: {status}",
        f"Duration: {duration:.1f}s" if duration is not None else "Duration: unknown",
        f"Shots: {len(shots)}",
        f"Creator: {_creator_line(instagram)}",
        f"Metrics: {_metrics_line(instagram)}",
        f"Instagram audio: {_audio_line(instagram)}",
        "",
        "## Contact Sheet",
        "",
    ]
    if contact_sheet:
        lines.append(f"![Contact sheet]({_artifact_link_path(contact_sheet, out_dir)})")
    else:
        lines.append("Contact sheet was not generated in this environment.")
    transcript_excerpt = transcript_text.strip()
    if len(transcript_excerpt) > 900:
        transcript_excerpt = transcript_excerpt[:897].rstrip() + "..."
    lines.extend([
        "",
        "## On-Screen Text",
        "",
        _format_on_screen_text(on_screen_text),
        "",
        "## Audio Transcript",
        "",
        transcript_excerpt or "No transcript generated.",
        "",
        "## Visual",
        "",
        visual_summary,
        "",
        "## Strategy",
        "",
        strategy_summary,
        "",
        "## Audience",
        "",
        comments_note,
        "",
        "## Ideas",
        "",
    ])
    if ideas:
        for idea in ideas[:5]:
            if isinstance(idea, dict):
                title = idea.get("title") or "Untitled idea"
                description = idea.get("description") or ""
                lines.append(f"- {title}: {description}")
    else:
        lines.append("No ideas generated.")
    return "\n".join(str(line) for line in lines)


def analyze_source(
    source: str,
    out_dir: Path,
    ai: str = "auto",
    transcribe: TranscribeMode = "auto",
    transcribe_model: str = DEFAULT_LOCAL_TRANSCRIBE_MODEL,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    progress = progress or _noop_progress
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir = out_dir / "keyframes"

    progress("Resolving AI mode.")
    ai_mode, ai_error = resolve_ai_mode(ai)
    video_path = None
    acquisition = "unknown"
    analysis_warning = None
    if ai_mode is None:
        analysis_warning = ai_error

    try:
        progress("Acquiring video.")
        video_path, acquisition = acquire_video(source, out_dir)
        progress("Reading video duration.")
        duration = ffprobe_duration(video_path)
        progress("Detecting shots.")
        shots = detect_shots(video_path)
        progress("Extracting keyframes.")
        keyframes = extract_keyframes(video_path, shots, keyframes_dir)
        progress("Creating contact sheet.")
        contact_sheet = create_contact_sheet(video_path, out_dir / "contact_sheet.jpg", duration=duration)
        progress("Fetching Instagram metadata when available.")
        instagram_metadata, instagram_session_used = _fetch_instagram_metadata(source)
    except Exception as exc:
        manifest = {
            "content_ai_version": __version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "source_kind": "url" if is_url(source) else "file",
            "status": "failed",
            "error": str(exc),
            "ai_mode": ai_mode,
        }
        _write_json(out_dir / "manifest.json", manifest)
        return 1, str(exc)

    visual_summary = "Deterministic pass complete. AI visual analysis was not run."
    strategy_summary = "Run with agent mode, Codex CLI, or an API key configured to generate strategy."
    on_screen_text: list[str] = []
    ideas = []
    transcript_text = ""
    transcript_segments: list[dict] = []
    transcription_engine = None
    transcription_warning = None

    progress("Running transcription.")
    transcript_text, transcript_segments, transcription_engine, transcription_warning = _run_transcription(
        video_path,
        transcribe,
        transcribe_model,
    )

    if not transcript_text.strip():
        transcription_warning = transcription_warning or _transcription_required_message(transcribe, transcribe_model)
        manifest = {
            "content_ai_version": __version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "source_kind": "url" if is_url(source) else "file",
            "status": "needs_transcription_setup",
            "acquisition": acquisition,
            "ai_mode": ai_mode,
            "warning": transcription_warning,
            "transcription": {
                "requested": transcribe,
                "engine": transcription_engine,
                "model": transcribe_model if transcription_engine == "local" else None,
                "warning": transcription_warning,
            },
            "capabilities": {
                "video_acquired": True,
                "shot_detection": True,
                "keyframes": bool(keyframes),
                "transcription": False,
                "instagram_session_used": instagram_session_used,
                "instagram_metadata": instagram_metadata.get("status") == "fetched",
                "instagram_creator": _has_present_value(instagram_metadata.get("creator") or {}),
                "instagram_metrics": _has_present_value(instagram_metadata.get("metrics") or {}),
            },
            "artifacts": REQUIRED_ARTIFACTS,
            "optional_artifacts": ["contact_sheet.jpg"] if contact_sheet else [],
        }
        reel = _build_reel_payload(source, video_path, duration, shots, instagram_metadata)
        _write_json(out_dir / "manifest.json", manifest)
        _write_json(out_dir / "reel.json", reel)
        _write_json(out_dir / "media.json", _build_media_payload(source, video_path, duration, instagram_metadata))
        _write_json(out_dir / "shots.json", {"shots": shots})
        _write_text(out_dir / "transcript.md", f"# Transcript\n\n{transcription_warning}")
        _write_text(out_dir / "transcript.srt", _empty_srt())
        _write_text(out_dir / "onscreen_text.md", "# On-Screen Text\n\nSkipped because transcription is required first.")
        _write_text(out_dir / "visual.md", "# Visual Analysis\n\nSkipped because transcription is required first.")
        _write_json(out_dir / "comments.json", {"comments": [], "source": "not_fetched"})
        _write_text(out_dir / "audience.md", "# Audience\n\nNo comments fetched for this analysis.")
        _write_text(out_dir / "strategy.md", "# Strategy\n\nSkipped because transcription is required first.")
        _write_json(out_dir / "ideas.json", {"ideas": []})
        _write_text(
            out_dir / "report.md",
            _render_report(
                source=source,
                out_dir=out_dir,
                status="needs_transcription_setup",
                duration=duration,
                shots=shots,
                instagram=instagram_metadata,
                transcript_text=transcription_warning,
                on_screen_text=[],
                visual_summary="Skipped because transcription is required first.",
                strategy_summary="Skipped because transcription is required first.",
                ideas=[],
                contact_sheet=contact_sheet,
                comments_note="No comments fetched for this analysis.",
            ),
        )
        return 2, transcription_warning

    analysis_images = ([contact_sheet] if contact_sheet else []) + keyframes[:6]

    if ai_mode == "agent":
        progress("Writing agent handoff request.")
        _write_agent_handoff(
            out_dir,
            source=source,
            shots=shots,
            contact_sheet=contact_sheet,
            keyframes=keyframes,
        )
        visual_summary = "Agent synthesis pending. Read `agent_request.md`, inspect the visual artifacts, write `agent_response.json`, then run `shortform-ai agent apply`."
        strategy_summary = "Agent synthesis pending. The CLI prepared deterministic media, transcript, metadata, shots, keyframes, and schema artifacts for the active assistant."
        analysis_warning = "Agent handoff written. Complete synthesis in the active agent interface and apply `agent_response.json`."
    elif ai_mode == "codex" and analysis_images:
        try:
            progress("Running Codex visual and strategy analysis.")
            codex_result = run_codex_json(
                analysis_dir=out_dir,
                images=analysis_images,
                prompt=_analysis_prompt(shots),
            )
            visual_summary = str(codex_result.get("visual_summary") or visual_summary)
            strategy_summary = str(codex_result.get("strategy_summary") or strategy_summary)
            raw_on_screen_text = codex_result.get("on_screen_text")
            if isinstance(raw_on_screen_text, list):
                on_screen_text = [str(line) for line in raw_on_screen_text if str(line).strip()]
            raw_ideas = codex_result.get("ideas")
            if isinstance(raw_ideas, list):
                ideas = raw_ideas
        except Exception as exc:
            analysis_warning = (
                f"Codex CLI analysis failed ({exc}). "
                "Set OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY for API fallback."
            )
            if ai == "codex":
                ai_mode = None
    elif ai_mode == "api":
        if keyframes:
            try:
                progress("Running API visual and strategy analysis.")
                openai_result = run_openai_vision_json(
                    images=analysis_images,
                    prompt=_analysis_prompt(shots),
                )
                visual_summary = str(openai_result.get("visual_summary") or visual_summary)
                strategy_summary = str(openai_result.get("strategy_summary") or strategy_summary)
                raw_on_screen_text = openai_result.get("on_screen_text")
                if isinstance(raw_on_screen_text, list):
                    on_screen_text = [str(line) for line in raw_on_screen_text if str(line).strip()]
                raw_ideas = openai_result.get("ideas")
                if isinstance(raw_ideas, list):
                    ideas = raw_ideas
            except Exception as exc:
                analysis_warning = f"OpenAI vision analysis failed ({exc})."
                if ai == "api":
                    ai_mode = None

    manifest = {
        "content_ai_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "source_kind": "url" if is_url(source) else "file",
        "status": "needs_agent_synthesis" if ai_mode == "agent" else ("complete" if ai_mode else "needs_ai_setup"),
        "acquisition": acquisition,
        "ai_mode": ai_mode,
        "warning": analysis_warning,
        "transcription": {
            "requested": transcribe,
            "engine": transcription_engine,
            "model": transcribe_model if transcription_engine == "local" else None,
            "warning": transcription_warning,
        },
        "capabilities": {
            "video_acquired": True,
            "shot_detection": True,
            "keyframes": bool(keyframes),
            "transcription": bool(transcript_text),
            "agent_synthesis": False if ai_mode == "agent" else bool(ai_mode),
            "instagram_session_used": instagram_session_used,
            "instagram_metadata": instagram_metadata.get("status") == "fetched",
            "instagram_creator": _has_present_value(instagram_metadata.get("creator") or {}),
            "instagram_metrics": _has_present_value(instagram_metadata.get("metrics") or {}),
        },
        "artifacts": REQUIRED_ARTIFACTS,
        "optional_artifacts": (["contact_sheet.jpg"] if contact_sheet else []) + (AGENT_ARTIFACTS if ai_mode == "agent" else []),
    }

    reel = _build_reel_payload(source, video_path, duration, shots, instagram_metadata)

    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "reel.json", reel)
    _write_json(out_dir / "media.json", _build_media_payload(source, video_path, duration, instagram_metadata))
    _write_json(out_dir / "shots.json", {"shots": shots})
    _write_text(out_dir / "transcript.md", f"# Audio Transcript\n\n{transcript_text or 'No transcript generated in this local runtime pass.'}")
    _write_text(out_dir / "transcript.srt", _segments_to_srt(transcript_segments))
    _write_text(out_dir / "onscreen_text.md", f"# On-Screen Text\n\n{_format_on_screen_text(on_screen_text)}")
    _write_text(out_dir / "visual.md", f"# Visual Analysis\n\n{visual_summary}")
    _write_json(out_dir / "comments.json", {"comments": [], "source": "not_fetched"})
    _write_text(out_dir / "audience.md", "# Audience\n\nNo comments fetched for this analysis.")
    _write_text(out_dir / "strategy.md", f"# Strategy\n\n{strategy_summary}")
    _write_json(out_dir / "ideas.json", {"ideas": ideas})
    _write_text(
        out_dir / "report.md",
        _render_report(
            source=source,
            out_dir=out_dir,
            status="needs_agent_synthesis" if ai_mode == "agent" else ("complete" if ai_mode else "needs_ai_setup"),
            duration=duration,
            shots=shots,
            instagram=instagram_metadata,
            transcript_text=transcript_text,
            on_screen_text=on_screen_text,
            visual_summary=visual_summary,
            strategy_summary=strategy_summary,
            ideas=ideas,
            contact_sheet=contact_sheet,
            comments_note="No comments fetched for this analysis.",
        ),
    )

    if ai_mode == "agent":
        message = (
            f"agent handoff written to {out_dir}. "
            "Read agent_request.md, write agent_response.json, then run "
            f"`shortform-ai agent apply {out_dir}`."
        )
        if transcription_warning:
            message = f"{message}\n{transcription_warning}"
        return 0, message
    if ai_mode is None:
        return 2, analysis_warning or "AI setup required"
    message = f"analysis written to {out_dir}"
    if transcription_warning:
        message = f"{message}\n{transcription_warning}"
    return 0, message
