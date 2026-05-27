from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from content_ai_runtime.cli import _merge_comments_into_analysis_report, build_parser, main
from content_ai_runtime.skill import install_skill


class CLITests(TestCase):
    def test_parses_analyze_command(self):
        args = build_parser().parse_args([
            "analyze",
            "video.mp4",
            "--out",
            "/tmp/out",
            "--ai",
            "agent",
            "--transcribe",
            "local",
            "--transcribe-model",
            "small",
        ])
        self.assertEqual(args.source, "video.mp4")
        self.assertEqual(args.ai, "agent")
        self.assertEqual(args.transcribe, "local")
        self.assertEqual(args.transcribe_model, "small")

    def test_parses_instagram_auth_flags(self):
        args = build_parser().parse_args(["auth", "instagram", "--from-chrome"])
        self.assertTrue(args.from_chrome)

    def test_parses_bootstrap_analyze_command(self):
        args = build_parser().parse_args([
            "bootstrap",
            "analyze",
            "https://www.instagram.com/reels/DYvyjVYSbAZ/",
            "--instagram-from-chrome",
            "--comments-count",
            "25",
        ])
        self.assertEqual(args.source, "https://www.instagram.com/reels/DYvyjVYSbAZ/")
        self.assertEqual(args.ai, "auto")
        self.assertTrue(args.instagram_from_chrome)
        self.assertEqual(args.comments_count, 25)

    def test_parses_agent_apply_command(self):
        args = build_parser().parse_args(["agent", "apply", "/tmp/analysis", "--from", "/tmp/response.json"])

        self.assertEqual(args.analysis_dir, "/tmp/analysis")
        self.assertEqual(args.response_path, "/tmp/response.json")

    @mock.patch("content_ai_runtime.cli.read_instagram_session", return_value="1234:secret")
    @mock.patch("content_ai_runtime.cli.backend_name", return_value="test-store")
    def test_doctor_redacts_session(self, _backend, _session):
        with mock.patch("builtins.print") as mocked_print:
            code = main(["doctor"])
        self.assertEqual(code, 0)
        payload = json.loads(mocked_print.call_args.args[0])
        self.assertNotIn("1234:secret", json.dumps(payload))
        self.assertEqual(payload["shortform_ai_version"], payload["content_ai_version"])
        self.assertEqual(payload["instagram_auth"]["session_preview"], "1234…[REDACTED]")
        self.assertEqual(payload["local_transcription"]["default_model"], "base")
        self.assertIn("pipx_install", payload["local_transcription"])
        self.assertIn("setup", payload["local_transcription"])
        self.assertIn("pipx_inject", payload["local_transcription"])

    @mock.patch.dict("os.environ", {"SHORTFORM_AI_INSTAGRAM_SESSIONID_INPUT": "12345:manual"}, clear=True)
    @mock.patch("content_ai_runtime.cli.write_instagram_session", return_value="test-store")
    def test_manual_auth_uses_env_input_for_noninteractive_tests(self, write_secret):
        code = main(["auth", "instagram", "--manual", "--allow-insecure-file-storage"])
        self.assertEqual(code, 0)
        write_secret.assert_called_once()

    @mock.patch("content_ai_runtime.cli._install_local_transcription", return_value=(True, "installed"))
    def test_setup_local_transcription_command(self, install):
        code = main(["setup", "transcription", "--local"])

        self.assertEqual(code, 0)
        install.assert_called_once()

    @mock.patch("content_ai_runtime.cli._install_local_transcription", return_value=(True, "installed"))
    @mock.patch("content_ai_runtime.cli._local_transcription_available", return_value=False)
    @mock.patch("content_ai_runtime.cli._has_openai_transcription_key", return_value=False)
    def test_bootstrap_auto_mode_installs_local_transcription_without_openai(self, _openai, _local, install):
        from content_ai_runtime.cli import _maybe_install_local_transcription_for_run

        payload = _maybe_install_local_transcription_for_run("auto")

        self.assertTrue(payload["needed"])
        self.assertTrue(payload["attempted"])
        self.assertTrue(payload["ok"])
        install.assert_called_once()

    @mock.patch("content_ai_runtime.cli.read_instagram_session", return_value="1234:secret")
    @mock.patch("content_ai_runtime.cli.WebInstagramClient")
    def test_comments_fetch_writes_artifacts(self, client_cls, _session):
        client_cls.return_value.get_media_comments.return_value = [
            {
                "text": "Great hook",
                "username": "viewer",
                "like_count": 3,
                "created_at": 1,
                "is_verified": False,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "comments",
                "https://www.instagram.com/reel/DWkQ7wbip37/",
                "--out",
                tmp,
            ])

            self.assertEqual(code, 0)
            payload = json.loads((Path(tmp) / "comments.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["comments"][0]["text"], "Great hook")
            self.assertIn("@viewer", (Path(tmp) / "audience.md").read_text(encoding="utf-8"))

    @mock.patch("content_ai_runtime.cli.read_instagram_session", return_value=None)
    def test_comments_require_instagram_auth(self, _session):
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "comments",
                "https://www.instagram.com/reel/DWkQ7wbip37/",
                "--out",
                tmp,
            ])
        self.assertEqual(code, 1)

    def test_merge_comments_updates_root_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "analysis"
            comments = root / "comments"
            comments.mkdir(parents=True)
            (root / "report.md").write_text(
                "# Shortform AI Report\n\n## Audience\n\nNo comments fetched.\n\n## Ideas\n\n- A\n",
                encoding="utf-8",
            )
            (comments / "audience.md").write_text(
                "# Audience\n\nFetched 1 comments.\n\nTop comments by likes:\n- @viewer: Great hook (3 likes)\n",
                encoding="utf-8",
            )
            (comments / "comments.json").write_text('{"count": 1}\n', encoding="utf-8")

            _merge_comments_into_analysis_report(root, comments)

            self.assertEqual(json.loads((root / "comments.json").read_text(encoding="utf-8"))["count"], 1)
            report = (root / "report.md").read_text(encoding="utf-8")
            self.assertIn("Fetched 1 comments.", report)
            self.assertIn("## Ideas", report)
            self.assertNotIn("No comments fetched.", report)

    @mock.patch("content_ai_runtime.cli.read_instagram_session", return_value="1234:secret")
    @mock.patch("content_ai_runtime.cli.WebInstagramClient")
    def test_account_fetch_writes_artifacts(self, client_cls, _session):
        client = client_cls.return_value
        client.get_user_profile.return_value = {
            "pk": "42",
            "username": "creator",
            "full_name": "Creator",
            "follower_count": 100,
            "following_count": 10,
            "media_count": 5,
        }
        client.get_user_feed_page.return_value = ([
            {
                "pk": 1,
                "code": "POST",
                "caption": {"text": "caption"},
                "like_count": 7,
                "comment_count": 2,
            }
        ], None)

        with tempfile.TemporaryDirectory() as tmp:
            code = main(["account", "@creator", "--out", tmp])

            self.assertEqual(code, 0)
            payload = json.loads((Path(tmp) / "account.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["profile"]["username"], "creator")
            self.assertEqual(payload["recent_media"][0]["code"], "POST")
            self.assertIn("# @creator", (Path(tmp) / "account.md").read_text(encoding="utf-8"))

    @mock.patch("content_ai_runtime.cli._maybe_install_local_transcription_for_run", return_value={"needed": True, "attempted": False, "ok": True, "message": "test"})
    @mock.patch("content_ai_runtime.cli._write_comments_for_url", return_value=(0, "comments ok"))
    @mock.patch("content_ai_runtime.cli.analyze_source", return_value=(0, "analysis ok"))
    @mock.patch("content_ai_runtime.cli.read_instagram_session", return_value="1234:secret")
    def test_bootstrap_analyze_orchestrates_without_secret_leak(self, _session, analyze, comments, _local_transcription):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "analysis"
            skill_target = Path(tmp) / "skills"

            code = main([
                "bootstrap",
                "analyze",
                "https://www.instagram.com/reel/DYvyjVYSbAZ/",
                "--out",
                str(out),
                "--skill-target",
                str(skill_target),
            ])

            self.assertEqual(code, 0)
            analyze.assert_called_once()
            comments.assert_called_once()
            self.assertTrue((skill_target / "shortform-ai" / "SKILL.md").exists())
            payload = json.loads((out / "bootstrap.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["analyze"]["exit_code"], 0)
            self.assertEqual(payload["comments"]["exit_code"], 0)
            self.assertNotIn("1234:secret", json.dumps(payload))

    @mock.patch("content_ai_runtime.cli._maybe_install_local_transcription_for_run", return_value={"needed": True, "attempted": False, "ok": True, "message": "test"})
    @mock.patch("content_ai_runtime.cli._import_instagram_from_chrome", return_value=(True, "imported"))
    @mock.patch("content_ai_runtime.cli._write_comments_for_url", return_value=(0, "comments ok"))
    @mock.patch("content_ai_runtime.cli.analyze_source", return_value=(0, "analysis ok"))
    @mock.patch("content_ai_runtime.cli.read_instagram_session", side_effect=[None, None, "1234:secret"])
    def test_bootstrap_can_import_instagram_from_chrome(self, _session, _analyze, _comments, import_auth, _local_transcription):
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "bootstrap",
                "analyze",
                "https://www.instagram.com/reel/DYvyjVYSbAZ/",
                "--out",
                str(Path(tmp) / "analysis"),
                "--skill-target",
                str(Path(tmp) / "skills"),
                "--instagram-from-chrome",
            ])

            self.assertEqual(code, 0)
            import_auth.assert_called_once()

    def test_skill_install_creates_named_skill_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = install_skill(Path(tmp))

            self.assertEqual(path, Path(tmp).resolve() / "shortform-ai" / "SKILL.md")
            text = path.read_text(encoding="utf-8")
            self.assertIn("name: shortform-ai", text)
            self.assertIn("pipx install shortform-ai", text)
            self.assertIn("PY312=", text)
            self.assertIn('Do not use plain `python3` on macOS', text)
            self.assertIn("pip install --upgrade shortform-ai yt-dlp", text)
            self.assertIn("shortform-ai bootstrap analyze", text)
            self.assertIn("default AI mode", text)
            self.assertIn("is agent mode", text)
            self.assertIn("shortform-ai bootstrap analyze <instagram-url-or-file> --instagram-from-chrome", text)
            self.assertIn("shortform-ai agent apply", text)
            self.assertIn(".venv-shortform-ai/bin/shortform-ai doctor", text)
            self.assertIn("shortform-ai setup transcription --local", text)
            self.assertIn("pipx install yt-dlp", text)
            self.assertIn("Do not use `npm`, `npx`, or `bunx`", text)

    def test_skill_install_accepts_direct_skill_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shortform-ai"
            path = install_skill(target)

            self.assertEqual(path, target.resolve() / "SKILL.md")
            self.assertIn("name: shortform-ai", path.read_text(encoding="utf-8"))
