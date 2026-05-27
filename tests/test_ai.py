from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock

from content_ai_runtime.ai import _extract_last_json_object, codex_command, load_env_files, resolve_ai_mode, run_openai_vision_json


class AITests(TestCase):
    def test_agent_mode_is_explicit_provider(self):
        mode, error = resolve_ai_mode("agent")

        self.assertEqual(mode, "agent")
        self.assertIsNone(error)

    @mock.patch("content_ai_runtime.ai.shutil.which", return_value="/usr/bin/codex")
    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1"}, clear=True)
    def test_auto_defaults_to_agent_even_when_codex_exists(self, _which):
        mode, error = resolve_ai_mode("auto")
        self.assertEqual(mode, "agent")
        self.assertIsNone(error)

    @mock.patch("content_ai_runtime.ai.shutil.which", return_value=None)
    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1", "OPENAI_API_KEY": "sk-test"}, clear=True)
    def test_auto_defaults_to_agent_even_when_api_key_exists(self, _which):
        mode, error = resolve_ai_mode("auto")
        self.assertEqual(mode, "agent")
        self.assertIsNone(error)

    @mock.patch("content_ai_runtime.ai.shutil.which", return_value=None)
    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1"}, clear=True)
    def test_auto_defaults_to_agent_without_provider(self, _which):
        mode, error = resolve_ai_mode("auto")
        self.assertEqual(mode, "agent")
        self.assertIsNone(error)

    @mock.patch("content_ai_runtime.ai.shutil.which", return_value="/usr/bin/codex")
    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1"}, clear=True)
    def test_explicit_codex_uses_codex_cli(self, _which):
        mode, error = resolve_ai_mode("codex")
        self.assertEqual(mode, "codex")
        self.assertIsNone(error)

    @mock.patch("content_ai_runtime.ai.shutil.which", return_value=None)
    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1", "OPENAI_API_KEY": "sk-test"}, clear=True)
    def test_explicit_api_uses_provider_key(self, _which):
        mode, error = resolve_ai_mode("api")
        self.assertEqual(mode, "api")
        self.assertIsNone(error)

    def test_codex_command_includes_images_and_read_only_sandbox(self):
        cmd = codex_command(Path("/tmp/a"), [Path("keyframes/shot_001.jpg")], "prompt")
        self.assertIn("-s", cmd)
        self.assertIn("read-only", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("--ephemeral", cmd)
        self.assertIn("--image", cmd)
        self.assertIn("keyframes/shot_001.jpg", [str(part) for part in cmd])
        self.assertEqual(cmd[-1], "-")

    def test_extract_last_json_object_from_codex_output(self):
        data = _extract_last_json_object(
            'logs\n'
            '{"visual_summary":"outer","strategy_summary":"strategy","ideas":[{"title":"nested"}]}\n'
            'tokens used\n'
            '{"title":"nested"}\n'
        )
        self.assertEqual(data["visual_summary"], "outer")

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_load_env_files_reads_nearby_dotenv_without_printing(self):
        with mock.patch("content_ai_runtime.ai.Path.cwd") as cwd:
            with mock.patch("pathlib.Path.read_text", return_value="OPENAI_API_KEY=sk-from-file\n"):
                with mock.patch("pathlib.Path.exists", return_value=True), mock.patch("pathlib.Path.is_file", return_value=True):
                    cwd.return_value = Path("/tmp/project")
                    load_env_files()
        self.assertEqual(__import__("os").environ["OPENAI_API_KEY"], "sk-from-file")

    @mock.patch.dict("os.environ", {"SHORTFORM_AI_DISABLE_DOTENV": "1", "OPENAI_API_KEY": "sk-test"}, clear=True)
    @mock.patch("content_ai_runtime.ai.requests.post")
    def test_run_openai_vision_json_posts_schema_request(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"visual_summary":"v","strategy_summary":"s","on_screen_text":["caption"],"ideas":[]}'
                    }
                }
            ]
        }
        with mock.patch("content_ai_runtime.ai._image_data_url", return_value="data:image/jpeg;base64,abc"):
            data = run_openai_vision_json(images=[Path("/tmp/contact_sheet.jpg"), Path("/tmp/frame.jpg")], prompt="prompt")

        self.assertEqual(data["visual_summary"], "v")
        self.assertEqual(data["on_screen_text"], ["caption"])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["messages"][1]["content"][1]["image_url"]["detail"], "high")
        self.assertEqual(payload["messages"][1]["content"][2]["image_url"]["detail"], "low")
