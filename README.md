# shortform-ai

Local-first CLI for short-form video and creator analysis.

Recommended persistent install for repeated agent use:

```bash
pipx install shortform-ai
~/.local/bin/shortform-ai doctor
```

If the app is already installed with pipx:

```bash
pipx upgrade shortform-ai
```

Homebrew remains a good system install path once a tap is wired:

```bash
brew install shortform-ai
```

For one-off venv tests, use Python 3.12+ explicitly:

```bash
python3.12 -m venv .venv-shortform-ai
.venv-shortform-ai/bin/python -m pip install --upgrade pip
.venv-shortform-ai/bin/python -m pip install shortform-ai yt-dlp
```

Local transcription is installed on first use when no OpenAI key is configured:

```bash
shortform-ai setup transcription --local
```

`shortform-ai analyze` defaults to `--transcribe auto`. It uses OpenAI
transcription when `OPENAI_API_KEY` is configured; otherwise `bootstrap analyze`
installs local faster-whisper on first use. The default local model is `base`,
which downloads a model cache on first use. Use `--transcribe-model small` for
better local accuracy.

Analysis requires a real transcript. If neither OpenAI transcription nor local
faster-whisper is available, `analyze` stops with setup instructions instead of
producing a transcript-free strategy pass.

## Starter Prompt For Codex / Claude Code

Paste this into an agent harness after installing `shortform-ai`:

```text
Use the shortform-ai skill to analyze this reel:
{instagram-url}

Run the default agent-mode workflow, apply the agent response, and summarize the
final report with the contact sheet inline. Never print Instagram session
values.
```

## CLI Usage

For one-prompt agent workflows, use the bootstrap command after installing the
package:

```bash
shortform-ai bootstrap analyze {instagram-url-or-file} --instagram-from-chrome
```

It installs/updates the agent skill when a compatible skill target is detected,
optionally imports the user's own Chrome Instagram session, runs analysis,
fetches Instagram comments when possible, and writes `bootstrap.json` beside the
analysis artifacts. The analyzer emits progress lines during long-running steps
so the assistant does not appear stuck. When comment fetch succeeds, bootstrap
merges the audience summary back into the root `report.md`, `audience.md`, and
`comments.json`.

The default `--ai auto` mode is agent mode, designed for Codex, Claude Code, or
another assistant interface. The CLI performs deterministic media work and writes
`agent_request.md`, `agent_schema.json`, and `agent_response.example.json`
instead of shelling out to a nested agent CLI. The active assistant should read
the request, inspect the contact sheet/keyframes/transcript/comments, write
`agent_response.json`, then run:

```bash
shortform-ai agent apply {analysis-dir}
```

The existing `--ai codex` and `--ai api` modes remain available for users who
explicitly want nested Codex CLI execution or API-key automation.

For Instagram reel URLs with local Instagram auth configured, analysis also
writes safe creator, aggregate metrics, and audio attribution metadata to
`media.json`, `reel.json`, and `report.md`, including likes, comments,
plays/views, creator profile fields, and named background music/original-audio
title when Instagram exposes them.

Video analysis also tries to write `contact_sheet.jpg` and always writes
`report.md`, a rich Markdown overview that points at the contact sheet when
available. Caption-heavy reels also get `onscreen_text.md`; `transcript.md` is
the audio transcript, so it may contain music lyrics instead of the visible
story text.

Development usage from a source checkout:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/shortform-ai doctor
```

The CLI reads a user's own local Instagram Chrome session only after an
explicit `shortform-ai auth instagram --from-chrome` command. Session values are
redacted from output and are stored in local secure storage when available.

`content-ai` remains available as a backwards-compatible command alias.
