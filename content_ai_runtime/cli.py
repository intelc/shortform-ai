from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .ai import PLACEHOLDER_KEYS, detect_ai, load_env_files
from .analyze import DEFAULT_LOCAL_TRANSCRIBE_MODEL, analyze_source, apply_agent_response
from .instagram_cookies import InstagramSession, list_instagram_sessions
from .instagram_client import (
    WebInstagramClient,
    WebInstagramClientError,
    extract_shortcode_from_url,
    shortcode_to_media_id,
    summarize_feed_item,
)
from .media import have_command
from .paths import APP_DIR, CONFIG_PATH, RUNTIME_DIR, ensure_app_dir, ensure_runtime_dir, load_config, save_config
from .redact import redact_secret
from .secrets import (
    SecretStoreError,
    backend_name,
    delete_instagram_session,
    read_instagram_session,
    write_instagram_session,
)
from .skill import install_skill


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _progress(message: str) -> None:
    print(f"[shortform-ai] {message}", file=sys.stderr, flush=True)


def _local_transcription_available() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("faster_whisper") is not None


def _has_openai_transcription_key() -> bool:
    load_env_files()
    return os.getenv("OPENAI_API_KEY", "").strip() not in PLACEHOLDER_KEYS


def _install_local_transcription() -> tuple[bool, str]:
    if _local_transcription_available():
        return True, "Local transcription is already available."

    requirement = f"shortform-ai[local-transcribe]>={__version__}"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", requirement]
    _progress("Installing local transcription engine (faster-whisper). This can take a few minutes.")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return (
            False,
            "Local transcription install timed out. Run `shortform-ai setup transcription --local` again, "
            "or manually run `pipx inject --force shortform-ai 'shortform-ai[local-transcribe]'`.",
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "pip exited without details"
        return (
            False,
            "Local transcription install failed. Run "
            "`shortform-ai setup transcription --local` again, or manually run "
            "`pipx inject --force shortform-ai 'shortform-ai[local-transcribe]'`. "
            f"Last pip output: {tail}",
        )
    if not _local_transcription_available():
        return (
            False,
            "Local transcription install finished, but faster-whisper is still not importable. "
            "Restart the shell or reinstall with `pipx inject --force shortform-ai 'shortform-ai[local-transcribe]'`.",
        )
    return True, "Local transcription engine installed."


def _maybe_install_local_transcription_for_run(transcribe: str) -> dict[str, object]:
    if transcribe in {"api", "none"}:
        return {"needed": False, "attempted": False, "ok": True, "message": "Local transcription not requested."}
    if transcribe == "auto" and _has_openai_transcription_key():
        return {"needed": False, "attempted": False, "ok": True, "message": "OPENAI_API_KEY detected; local transcription install skipped."}
    if _local_transcription_available():
        return {"needed": True, "attempted": False, "ok": True, "message": "Local transcription is already available."}
    ok, message = _install_local_transcription()
    return {"needed": True, "attempted": True, "ok": ok, "message": message}


def cmd_doctor(_args: argparse.Namespace) -> int:
    ai = detect_ai()
    session = read_instagram_session()
    result = {
        "content_ai_version": __version__,
        "shortform_ai_version": __version__,
        "platform": platform.platform(),
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "runtime_path": str(RUNTIME_DIR),
        "runtime_exists": RUNTIME_DIR.exists(),
        "ffmpeg": have_command("ffmpeg"),
        "ffprobe": have_command("ffprobe"),
        "yt_dlp": have_command("yt-dlp"),
        "python_cryptography": importlib.util.find_spec("cryptography") is not None,
        "local_transcription": {
            "faster_whisper": _local_transcription_available(),
            "default_model": DEFAULT_LOCAL_TRANSCRIBE_MODEL,
            "setup": "shortform-ai setup transcription --local",
            "install": "pip install --upgrade 'shortform-ai[local-transcribe]'",
            "pipx_install": "pipx install shortform-ai",
            "pipx_inject": "pipx inject --force shortform-ai 'shortform-ai[local-transcribe]'",
            "note": "Base installs are lightweight. Bootstrap will install local transcription on first use when no OPENAI_API_KEY is configured.",
        },
        "codex": bool(ai.codex_path),
        "codex_path": ai.codex_path,
        "api_keys": list(ai.api_keys),
        "instagram_auth": {
            "configured": bool(session),
            "storage": backend_name(),
            "session_preview": redact_secret(session),
        },
    }
    _print_json(result)
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if getattr(args, "setup_target", None) == "transcription":
        if not getattr(args, "local", False):
            print("Use `shortform-ai setup transcription --local` to install local transcription.", file=sys.stderr)
            return 2
        ok, message = _install_local_transcription()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    ensure_app_dir()
    ensure_runtime_dir()
    config = load_config()
    config.setdefault("version", 1)
    config.setdefault("default_ai", "auto")
    save_config(config)
    print(f"Created/updated {CONFIG_PATH}")
    return 0


def _choose_session(sessions: list[InstagramSession]) -> InstagramSession | None:
    if not sessions:
        return None
    if len(sessions) == 1:
        return sessions[0]
    print("Multiple Instagram sessions were found:")
    for idx, session in enumerate(sessions, 1):
        print(f"  {idx}. user_id={session.user_id} chrome_profile={session.chrome_profile}")
    while True:
        raw = input("Choose a session number: ").strip()
        try:
            selected = int(raw)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= selected <= len(sessions):
            return sessions[selected - 1]
        print("Enter a number from the list.")


def cmd_auth_instagram(args: argparse.Namespace) -> int:
    if args.status:
        session = read_instagram_session()
        _print_json({
            "configured": bool(session),
            "storage": backend_name(),
            "session_preview": redact_secret(session),
        })
        return 0

    if args.logout:
        delete_instagram_session()
        print("Instagram session removed from local shortform-ai storage.")
        return 0

    if args.from_chrome:
        sessions, err = list_instagram_sessions()
        if err:
            print(err, file=sys.stderr)
            print("Use `shortform-ai auth instagram --manual` as a fallback.", file=sys.stderr)
            return 1
        selected = _choose_session(sessions)
        if selected is None:
            print("No Instagram session found in Chrome.", file=sys.stderr)
            return 1
        try:
            storage = write_instagram_session(
                selected.sessionid,
                allow_fallback=args.allow_insecure_file_storage,
            )
        except SecretStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Instagram session imported from Chrome profile {selected.chrome_profile} into {storage}.")
        return 0

    if args.manual:
        sessionid = os.getenv("SHORTFORM_AI_INSTAGRAM_SESSIONID_INPUT") or os.getenv("CONTENT_AI_INSTAGRAM_SESSIONID_INPUT")
        if not sessionid:
            sessionid = getpass.getpass("Paste your Instagram sessionid: ").strip()
        if not sessionid or ":" not in sessionid:
            print("That does not look like an Instagram sessionid.", file=sys.stderr)
            return 1
        try:
            storage = write_instagram_session(
                sessionid,
                allow_fallback=args.allow_insecure_file_storage,
            )
        except SecretStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Instagram session stored in {storage}.")
        return 0

    print("Choose one of --from-chrome, --manual, --status, or --logout.", file=sys.stderr)
    return 2


def _import_instagram_from_chrome(*, allow_insecure_file_storage: bool = False) -> tuple[bool, str]:
    sessions, err = list_instagram_sessions()
    if err:
        return False, f"{err} Use `shortform-ai auth instagram --manual` as a fallback."
    selected = _choose_session(sessions)
    if selected is None:
        return False, "No Instagram session found in Chrome."
    try:
        storage = write_instagram_session(
            selected.sessionid,
            allow_fallback=allow_insecure_file_storage,
        )
    except SecretStoreError as exc:
        return False, str(exc)
    return True, f"Instagram session imported from Chrome profile {selected.chrome_profile} into {storage}."


def cmd_analyze(args: argparse.Namespace) -> int:
    code, message = analyze_source(
        args.source,
        Path(args.out),
        ai=args.ai,
        transcribe=args.transcribe,
        transcribe_model=args.transcribe_model,
        progress=_progress,
    )
    stream = sys.stderr if code else sys.stdout
    print(message, file=stream)
    return code


def _write_comments_for_url(url: str, out: Path, count: int) -> tuple[int, str]:
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    session = read_instagram_session()
    if not session:
        return 1, "Instagram auth is not configured. Run `shortform-ai auth instagram --from-chrome`."

    shortcode = extract_shortcode_from_url(url)
    media_id = shortcode_to_media_id(shortcode) if shortcode else None
    if not media_id:
        return 1, "Could not extract an Instagram reel/post shortcode from the URL."

    try:
        client = WebInstagramClient(sessionid=session)
        comments_data = client.get_media_comments(str(media_id), count=count)
    except WebInstagramClientError as exc:
        return 1, str(exc)

    payload = {
        "source": url,
        "shortcode": shortcode,
        "media_id": str(media_id),
        "count": len(comments_data),
        "comments": comments_data,
    }
    (out / "comments.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    top = comments_data[:5]
    lines = [
        "# Audience",
        "",
        f"Fetched {len(comments_data)} comments for `{shortcode}`.",
    ]
    if top:
        lines.extend(["", "Top comments by likes:"])
        for comment in top:
            text = (comment.get("text") or "").replace("\n", " ").strip()
            if len(text) > 140:
                text = text[:137].rstrip() + "..."
            lines.append(f"- @{comment.get('username', '')}: {text} ({comment.get('like_count', 0)} likes)")
    else:
        lines.extend(["", "No comments were returned by Instagram for this media."])
    (out / "audience.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return 0, f"comment artifacts written to {out}"


def _merge_comments_into_analysis_report(analysis_dir: Path, comments_dir: Path) -> None:
    comments_json = comments_dir / "comments.json"
    audience_md = comments_dir / "audience.md"
    if comments_json.exists():
        shutil.copy2(comments_json, analysis_dir / "comments.json")
    if audience_md.exists():
        shutil.copy2(audience_md, analysis_dir / "audience.md")

    report_path = analysis_dir / "report.md"
    if not report_path.exists() or not audience_md.exists():
        return

    audience_text = audience_md.read_text(encoding="utf-8").strip()
    audience_body = re.sub(r"^# Audience\s*", "", audience_text).strip() or audience_text
    report = report_path.read_text(encoding="utf-8")
    replacement = f"## Audience\n\n{audience_body}\n\n"
    start = report.find("## Audience")
    end = report.find("\n## Ideas", start)
    if start != -1 and end != -1:
        report = report[:start] + replacement + report[end + 1 :]
    else:
        report = report.rstrip() + "\n\n" + replacement.rstrip() + "\n"
    report_path.write_text(report.rstrip() + "\n", encoding="utf-8")


def cmd_comments(args: argparse.Namespace) -> int:
    code, message = _write_comments_for_url(args.url, Path(args.out), args.count)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


def cmd_account(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    handle = args.handle.lstrip("@")
    session = read_instagram_session()
    if not session:
        print("Instagram auth is not configured. Run `shortform-ai auth instagram --from-chrome`.", file=sys.stderr)
        return 1

    try:
        client = WebInstagramClient(sessionid=session)
        profile = client.get_user_profile(handle)
        feed_items, next_max_id = client.get_user_feed_page(profile["pk"], count=args.limit)
    except WebInstagramClientError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    recent = [summarize_feed_item(item) for item in feed_items[: args.limit]]
    payload = {
        "handle": handle,
        "profile": profile,
        "recent_media": recent,
        "next_max_id": next_max_id,
    }
    (out / "account.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        f"# @{profile.get('username') or handle}",
        "",
        profile.get("full_name") or "",
        "",
        f"Followers: {profile.get('follower_count')}",
        f"Following: {profile.get('following_count')}",
        f"Media count: {profile.get('media_count')}",
        "",
        "Recent media:",
    ]
    for item in recent:
        label = item.get("code") or item.get("pk")
        metrics = []
        for field in ("play_count", "view_count", "like_count", "comment_count"):
            if item.get(field) is not None:
                metrics.append(f"{field.replace('_', ' ')}: {item[field]}")
        lines.append(f"- {label}: {', '.join(metrics) if metrics else 'metrics unavailable'}")
    (out / "account.md").write_text("\n".join(str(line) for line in lines).rstrip() + "\n", encoding="utf-8")
    print(f"account artifact written to {out / 'account.json'}")
    return 0


def cmd_ideas(args: argparse.Namespace) -> int:
    analysis_dir = Path(args.analysis_dir).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    strategy = analysis_dir / "strategy.md"
    ideas = {
        "ideas": [
            {
                "title": "Remix The Strongest Opening",
                "description": "Use the first-shot/transcript evidence from the analysis directory to rewrite the hook in your own voice.",
                "reasoning": f"Grounded in {strategy.name if strategy.exists() else 'the analysis artifacts'}.",
            }
        ]
    }
    (out / "ideas.json").write_text(json.dumps(ideas, indent=2) + "\n", encoding="utf-8")
    print(f"ideas written to {out / 'ideas.json'}")
    return 0


def cmd_agent_prompt(args: argparse.Namespace) -> int:
    request_path = Path(args.analysis_dir).expanduser().resolve() / "agent_request.md"
    if not request_path.exists():
        print(f"agent request not found: {request_path}", file=sys.stderr)
        return 1
    print(request_path.read_text(encoding="utf-8"))
    return 0


def cmd_agent_apply(args: argparse.Namespace) -> int:
    response_path = Path(args.response_path).expanduser().resolve() if args.response_path else None
    code, message = apply_agent_response(Path(args.analysis_dir), response_path)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


def cmd_skill_install(args: argparse.Namespace) -> int:
    path = install_skill(Path(args.target))
    print(f"Installed shortform-ai skill at {path}")
    return 0


def _default_out_dir(source: str) -> Path:
    shortcode = extract_shortcode_from_url(source)
    if shortcode:
        slug = shortcode
    elif source.startswith(("http://", "https://")):
        slug = "analysis"
    else:
        slug = Path(source).stem or "analysis"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", slug).strip("-") or "analysis"
    return Path(".shortform-ai") / slug


def _default_skill_target() -> Path | None:
    explicit = os.getenv("SHORTFORM_AI_SKILL_TARGET")
    if explicit:
        return Path(explicit).expanduser()
    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    claude_home = os.getenv("CLAUDE_HOME") or os.getenv("CLAUDE_CONFIG_DIR")
    if claude_home:
        return Path(claude_home).expanduser() / "skills"
    if os.getenv("CLAUDECODE") or os.getenv("CLAUDE_CODE"):
        return Path.home() / ".claude" / "skills"
    return None


def cmd_bootstrap_analyze(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser().resolve() if args.out else _default_out_dir(args.source).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    ensure_app_dir()
    ensure_runtime_dir()

    bootstrap: dict[str, object] = {
        "shortform_ai_version": __version__,
        "source": args.source,
        "out_dir": str(out),
        "checks": {
            "ffmpeg": have_command("ffmpeg"),
            "ffprobe": have_command("ffprobe"),
            "yt_dlp": have_command("yt-dlp"),
            "faster_whisper": _local_transcription_available(),
        },
        "skill": {"installed": False, "path": None},
        "local_transcription": None,
        "instagram_auth": {"attempted": bool(args.instagram_from_chrome), "configured": bool(read_instagram_session())},
        "analyze": None,
        "comments": None,
    }

    if not args.no_skill_install:
        skill_target = Path(args.skill_target).expanduser() if args.skill_target else _default_skill_target()
        if skill_target:
            skill_path = install_skill(skill_target)
            bootstrap["skill"] = {"installed": True, "path": str(skill_path)}
            print(f"Installed/updated shortform-ai skill at {skill_path}")
        else:
            bootstrap["skill"] = {
                "installed": False,
                "path": None,
                "message": "No agent skill target detected. Use `shortform-ai skill install --target <skills-dir>` or rerun bootstrap with --skill-target.",
            }

    if args.instagram_from_chrome:
        if read_instagram_session() and not args.refresh_instagram_auth:
            auth_ok, auth_message = True, "Instagram auth already configured; keeping existing local credential."
        else:
            auth_ok, auth_message = _import_instagram_from_chrome(
                allow_insecure_file_storage=args.allow_insecure_file_storage,
            )
        bootstrap["instagram_auth"] = {
            "attempted": True,
            "configured": bool(read_instagram_session()),
            "ok": auth_ok,
            "message": auth_message,
        }
        print(auth_message, file=sys.stdout if auth_ok else sys.stderr)

    local_transcription = _maybe_install_local_transcription_for_run(args.transcribe)
    bootstrap["local_transcription"] = local_transcription
    bootstrap["checks"]["faster_whisper"] = _local_transcription_available()
    if local_transcription.get("attempted"):
        message = str(local_transcription.get("message") or "")
        print(message, file=sys.stdout if local_transcription.get("ok") else sys.stderr)
    if local_transcription.get("ok") is False:
        bootstrap["analyze"] = {"exit_code": 2, "message": local_transcription.get("message")}
        (out / "bootstrap.json").write_text(json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"bootstrap summary written to {out / 'bootstrap.json'}")
        return 2

    analyze_code, analyze_message = analyze_source(
        args.source,
        out,
        ai=args.ai,
        transcribe=args.transcribe,
        transcribe_model=args.transcribe_model,
        progress=_progress,
    )
    bootstrap["analyze"] = {"exit_code": analyze_code, "message": analyze_message}
    print(analyze_message, file=sys.stderr if analyze_code else sys.stdout)

    comments_code = None
    if args.comments_count > 0 and extract_shortcode_from_url(args.source):
        _progress("Fetching Instagram comments.")
        comments_out = out / "comments"
        comments_code, comments_message = _write_comments_for_url(args.source, comments_out, args.comments_count)
        bootstrap["comments"] = {
            "exit_code": comments_code,
            "message": comments_message,
            "out_dir": str(comments_out),
        }
        print(comments_message, file=sys.stderr if comments_code else sys.stdout)
        if comments_code == 0:
            _progress("Updating report with comment summary.")
            _merge_comments_into_analysis_report(out, comments_out)
    elif args.comments_count > 0:
        bootstrap["comments"] = {"exit_code": None, "message": "Skipped comments because source is not an Instagram URL."}

    bootstrap["artifacts"] = {
        "report": str(out / "report.md") if (out / "report.md").exists() else None,
        "contact_sheet": str(out / "contact_sheet.jpg") if (out / "contact_sheet.jpg").exists() else None,
    }
    if (out / "agent_request.md").exists():
        bootstrap["next_steps"] = [
            f"Read {out / 'agent_request.md'}",
            "Inspect the contact sheet/keyframes, transcript, metadata, and comments listed there.",
            f"Write {out / 'agent_response.json'} in the active agent interface.",
            f"Run `shortform-ai agent apply {out}`.",
            "Use the updated report.md for the final inline answer and include contact_sheet.jpg when present.",
        ]
    else:
        bootstrap["next_steps"] = [
            f"Read artifacts in {out}",
            "Start from report.md and include contact_sheet.jpg inline when present.",
            "Verify details against manifest.json, reel.json, media.json, timeline.md, timeline.json, transcript.md, visual.md, strategy.md, ideas.json, and audience.md.",
        ]
    (out / "bootstrap.json").write_text(json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"bootstrap summary written to {out / 'bootstrap.json'}")
    if analyze_code:
        return analyze_code
    if comments_code not in (None, 0):
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shortform-ai")
    parser.add_argument("--version", action="version", version=f"shortform-ai {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    setup = sub.add_parser("setup")
    setup.add_argument("setup_target", nargs="?", choices=["transcription"])
    setup.add_argument("--local", action="store_true")
    setup.set_defaults(func=cmd_setup)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap_sub = bootstrap.add_subparsers(dest="bootstrap_command", required=True)
    bootstrap_analyze = bootstrap_sub.add_parser("analyze")
    bootstrap_analyze.add_argument("source")
    bootstrap_analyze.add_argument("--out")
    bootstrap_analyze.add_argument("--ai", choices=["auto", "agent", "codex", "api"], default="auto")
    bootstrap_analyze.add_argument("--transcribe", choices=["auto", "api", "local", "none"], default="auto")
    bootstrap_analyze.add_argument("--transcribe-model", default=DEFAULT_LOCAL_TRANSCRIBE_MODEL)
    bootstrap_analyze.add_argument("--instagram-from-chrome", action="store_true")
    bootstrap_analyze.add_argument("--refresh-instagram-auth", action="store_true")
    bootstrap_analyze.add_argument("--allow-insecure-file-storage", action="store_true")
    bootstrap_analyze.add_argument("--comments-count", type=int, default=50)
    bootstrap_analyze.add_argument("--skill-target")
    bootstrap_analyze.add_argument("--no-skill-install", action="store_true")
    bootstrap_analyze.set_defaults(func=cmd_bootstrap_analyze)

    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    instagram = auth_sub.add_parser("instagram")
    instagram.add_argument("--from-chrome", action="store_true")
    instagram.add_argument("--manual", action="store_true")
    instagram.add_argument("--status", action="store_true")
    instagram.add_argument("--logout", action="store_true")
    instagram.add_argument("--allow-insecure-file-storage", action="store_true")
    instagram.set_defaults(func=cmd_auth_instagram)

    analyze = sub.add_parser("analyze")
    analyze.add_argument("source")
    analyze.add_argument("--out", required=True)
    analyze.add_argument("--ai", choices=["auto", "agent", "codex", "api"], default="auto")
    analyze.add_argument("--transcribe", choices=["auto", "api", "local", "none"], default="auto")
    analyze.add_argument("--transcribe-model", default=DEFAULT_LOCAL_TRANSCRIBE_MODEL)
    analyze.set_defaults(func=cmd_analyze)

    comments = sub.add_parser("comments")
    comments.add_argument("url")
    comments.add_argument("--out", required=True)
    comments.add_argument("--count", type=int, default=50)
    comments.set_defaults(func=cmd_comments)

    account = sub.add_parser("account")
    account.add_argument("handle")
    account.add_argument("--out", required=True)
    account.add_argument("--limit", type=int, default=12)
    account.set_defaults(func=cmd_account)

    ideas = sub.add_parser("ideas")
    ideas.add_argument("analysis_dir")
    ideas.add_argument("--out", required=True)
    ideas.set_defaults(func=cmd_ideas)

    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_prompt = agent_sub.add_parser("prompt")
    agent_prompt.add_argument("analysis_dir")
    agent_prompt.set_defaults(func=cmd_agent_prompt)
    agent_apply = agent_sub.add_parser("apply")
    agent_apply.add_argument("analysis_dir")
    agent_apply.add_argument("--from", dest="response_path")
    agent_apply.set_defaults(func=cmd_agent_apply)

    skill = sub.add_parser("skill")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    install = skill_sub.add_parser("install")
    install.add_argument("--target", required=True)
    install.set_defaults(func=cmd_skill_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
