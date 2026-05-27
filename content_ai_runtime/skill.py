from __future__ import annotations

from pathlib import Path


SKILL_MD = """---
name: shortform-ai
description: Analyze short-form video and Instagram creator context with the local shortform-ai CLI. Use when the user asks to inspect a reel, understand why short-form content works, analyze comments/audience reaction, compare creators, or generate content ideas from a video.
---

# Shortform AI CLI Skill

Use the local `shortform-ai` CLI for short-form video analysis.

## Setup

If `shortform-ai` is not on `PATH`, install it from PyPI before continuing.
Do not use `npm`, `npx`, or `bunx`; the published package is Python/PyPI.

Best persistent setup for repeated agent runs:

```bash
pipx install shortform-ai
~/.local/bin/shortform-ai --version
~/.local/bin/shortform-ai doctor
```

If `pipx` says `shortform-ai` is already installed, update the existing app:

```bash
pipx upgrade shortform-ai
~/.local/bin/shortform-ai doctor
```

After `pipx` installs the CLI, use the absolute app path if the current shell
does not immediately find it:

```bash
~/.local/bin/shortform-ai --version
```

For Instagram URLs, `yt-dlp` must be available to the running CLI. If
`shortform-ai doctor` reports `"yt_dlp": false`, install it as its own app:

```bash
pipx install yt-dlp
```

Then rerun `shortform-ai doctor` and confirm `"yt_dlp": true`.

One-off local venv fallback, mostly for clean install tests or machines without
usable `pipx`: check for Python 3.12 first. Do not use plain `python3` on macOS
unless it is Python 3.12+, because Apple/system `python3` is often 3.9 and
cannot install `shortform-ai`.

```bash
PY312="$(command -v python3.12 || command -v /opt/homebrew/bin/python3.12 || command -v /usr/local/bin/python3.12)"
"$PY312" -m venv .venv-shortform-ai
.venv-shortform-ai/bin/python -m pip install --upgrade pip
.venv-shortform-ai/bin/python -m pip install --upgrade shortform-ai yt-dlp
.venv-shortform-ai/bin/shortform-ai --version
.venv-shortform-ai/bin/shortform-ai doctor
```

If `pipx` is unavailable but `uvx` is available, use:

```bash
uvx --from shortform-ai shortform-ai --version
```

Local transcription is intentionally installed on first use instead of during
the base CLI install. If no `OPENAI_API_KEY` is configured, `bootstrap analyze`
will install the local faster-whisper engine before analysis. If analysis stops
because transcription is not configured, ask the user whether they want to set
`OPENAI_API_KEY` or install local inference. For local inference:

```bash
shortform-ai setup transcription --local
```

Then rerun `shortform-ai doctor` and confirm `local_transcription.faster_whisper`
is `true` before analyzing video.

Use the absolute venv binary path for all later commands if installed into a
local venv.

## Workflow

Fast path:

```bash
shortform-ai bootstrap analyze <instagram-url-or-file> --instagram-from-chrome
```

Use `--out <dir>` when the user requests a specific output directory. Use
`--comments-count 0` to skip Instagram comments. The bootstrap command installs
or updates this agent skill when a compatible skill target is detected, runs
deterministic video preparation, fetches comments for Instagram URLs when auth
is configured, writes `bootstrap.json`, and emits progress lines while
long-running video download/transcription steps are active. The default AI mode
is agent mode: the CLI does not call nested Codex or Claude CLIs. It writes
`agent_request.md`, `agent_schema.json`, and `agent_response.example.json` so
the active assistant can do the visual and strategy synthesis inside the current
interface.

1. Run `shortform-ai doctor` if setup is uncertain.
   If using the one-prompt venv path, run:
   `.venv-shortform-ai/bin/shortform-ai doctor`
2. For an Instagram reel/post URL, prefer:
   `shortform-ai bootstrap analyze <instagram-url> --instagram-from-chrome`
3. For a local file or non-Instagram URL, run:
   `shortform-ai analyze <url-or-file> --out .shortform-ai/<slug>`
4. When `agent_request.md` exists, read it, inspect the listed artifacts and
   images, write `agent_response.json`, then run:
   `shortform-ai agent apply <analysis-dir>`
5. For audience reaction on an Instagram reel/post without rerunning analysis, run:
   `shortform-ai comments <instagram-url> --out .shortform-ai/<slug>/comments`
6. For creator/account context, run:
   `shortform-ai account <instagram-handle> --out .shortform-ai/<handle>`
7. Read these artifacts:
   - `manifest.json`
   - `reel.json`
   - `media.json`
   - `bootstrap.json`
   - `agent_request.md`
   - `agent_schema.json`
   - `agent_response.json`
   - `report.md`
   - `contact_sheet.jpg`
   - `shots.json`
   - `timeline.md`
   - `timeline.json`
   - `transcript.md`
   - `onscreen_text.md`
   - `visual.md`
   - `audience.md`
   - `strategy.md`
   - `ideas.json`
   - `comments.json`
   - `account.json`
8. Synthesize a concise creator-strategy answer grounded in the artifacts.

When `contact_sheet.jpg` exists, include it inline in the final answer with an
absolute local image path, for example:

```markdown
![Contact sheet](/absolute/path/to/contact_sheet.jpg)
```

In default agent mode, `report.md` is provisional until `agent_response.json`
is applied. Read `agent_request.md` first, inspect the visual evidence, write
the JSON response, run `shortform-ai agent apply <analysis-dir>`, then use the
updated `report.md` for the final answer.

Prefer `report.md` as the first read for a rich overview, then verify details
against `manifest.json`, `media.json`, `timeline.md`, `timeline.json`,
`onscreen_text.md`, the audio transcript, ideas, and comments. Treat
`timeline.md` as the reverse-engineered beat/edit timeline grounded in detected
shot boundaries; treat `onscreen_text.md` as the main narrative transcript for
caption-heavy reels; `transcript.md` is the audio track and may only contain
music lyrics or ambient speech. Include the creator and aggregate metrics from
the report or `media.json`/`reel.json` in the final inline answer. If a metric
such as views is unavailable or `null`, say it is unavailable; do not estimate
it from likes or comments.

When `media.json` or `reel.json` includes `instagram_audio`, mention the named
track/original-audio attribution in the summary. When it includes
`instagram_creator` or `instagram_metrics`, mention the creator handle/name and
available likes, comments, plays/views, posting time, follower count, or other
safe public metrics. Never print or ask for raw Instagram session values.

For one-prompt Instagram analysis, prefer `bootstrap analyze` with
`--instagram-from-chrome` when the user has explicitly authorized local
Instagram auth. Report the output directory and summarize the transcript,
visual evidence, audio attribution, comments, and ideas from files on disk.

## Instagram Auth

Only run `shortform-ai auth instagram --from-chrome` when the user explicitly asks to connect their own local Instagram session. Never print, copy, upload, or reveal session tokens. `comments` and `account` read from that local session only.

## Boundaries

Do not post, like, follow, DM, scrape unrelated accounts, or automate engagement. This tool is for analysis and strategy only.
"""


def install_skill(target: Path) -> Path:
    target = target.expanduser().resolve()
    skill_dir = target if target.name == "shortform-ai" else target / "shortform-ai"
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(SKILL_MD, encoding="utf-8")
    return path
