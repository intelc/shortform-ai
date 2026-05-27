from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from content_ai_runtime.analyze import REQUIRED_ARTIFACTS, analyze_source, apply_agent_response


class AnalyzeTests(TestCase):
    @mock.patch("content_ai_runtime.analyze._run_transcription", return_value=("", [], None, None))
    @mock.patch("content_ai_runtime.analyze.acquire_video")
    @mock.patch("content_ai_runtime.analyze.detect_shots")
    @mock.patch("content_ai_runtime.analyze.extract_keyframes")
    @mock.patch("content_ai_runtime.analyze.create_contact_sheet", return_value=None)
    @mock.patch("content_ai_runtime.analyze.ffprobe_duration", return_value=1.0)
    @mock.patch("content_ai_runtime.analyze.resolve_ai_mode", return_value=("api", None))
    @mock.patch("content_ai_runtime.analyze.read_instagram_session", return_value=None)
    def test_analyze_requires_transcription_before_completion(self, _session, _ai, _duration, _sheet, keyframes, shots, acquire, _transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "fixture.mp4"
            video.write_bytes(b"fake")
            acquire.return_value = (video, "local_file")
            shots.return_value = [{"shot_number": 1, "start_time": 0.0, "end_time": 1.0, "duration": 1.0}]
            keyframes.return_value = []

            code, message = analyze_source("fixture.mp4", tmp_path / "out", ai="api")

            self.assertEqual(code, 2)
            self.assertIn("Transcription is required", message)
            for name in REQUIRED_ARTIFACTS:
                self.assertTrue((tmp_path / "out" / name).exists(), name)
            self.assertTrue((tmp_path / "out" / "report.md").exists())
            manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "needs_transcription_setup")
            self.assertNotIn("12345:secret", json.dumps(manifest))
            self.assertFalse(manifest["capabilities"]["instagram_session_used"])
            self.assertIn("transcription", manifest["capabilities"])
            self.assertFalse(manifest["capabilities"]["transcription"])

    @mock.patch(
        "content_ai_runtime.analyze._run_transcription",
        return_value=(
            "hello world",
            [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            "local",
            "Using bundled local transcription inference with faster-whisper model `base`. For cloud transcription, set OPENAI_API_KEY and rerun with `--transcribe api`.",
        ),
    )
    @mock.patch("content_ai_runtime.analyze.acquire_video")
    @mock.patch("content_ai_runtime.analyze.detect_shots")
    @mock.patch("content_ai_runtime.analyze.extract_keyframes")
    @mock.patch("content_ai_runtime.analyze.create_contact_sheet")
    @mock.patch("content_ai_runtime.analyze.ffprobe_duration", return_value=1.0)
    @mock.patch("content_ai_runtime.analyze.resolve_ai_mode", return_value=("codex", None))
    @mock.patch(
        "content_ai_runtime.analyze.run_codex_json",
        return_value={"visual_summary": "v", "strategy_summary": "s", "on_screen_text": ["caption"], "ideas": []},
    )
    @mock.patch("content_ai_runtime.analyze.read_instagram_session", return_value=None)
    def test_analyze_records_local_transcription_warning(
        self,
        _session,
        _codex,
        _ai,
        _duration,
        sheet,
        keyframes,
        shots,
        acquire,
        transcribe,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "fixture.mp4"
            video.write_bytes(b"fake")
            keyframe = tmp_path / "frame.jpg"
            keyframe.write_bytes(b"fake")
            contact_sheet = tmp_path / "out" / "contact_sheet.jpg"
            sheet.return_value = contact_sheet
            acquire.return_value = (video, "local_file")
            shots.return_value = [{"shot_number": 1, "start_time": 0.0, "end_time": 1.0, "duration": 1.0}]
            keyframes.return_value = [keyframe]

            code, message = analyze_source(
                "fixture.mp4",
                tmp_path / "out",
                ai="codex",
                transcribe="local",
                transcribe_model="base",
            )

            self.assertEqual(code, 0)
            self.assertIn("OPENAI_API_KEY", message)
            transcribe.assert_called_once()
            manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
            self.assertTrue(manifest["capabilities"]["transcription"])
            self.assertEqual(manifest["transcription"]["engine"], "local")
            self.assertEqual(manifest["transcription"]["model"], "base")
            report = (tmp_path / "out" / "report.md").read_text()
            self.assertIn("![Contact sheet](contact_sheet.jpg)", report)
            self.assertIn("## On-Screen Text", report)
            self.assertIn("- caption", report)
            self.assertIn("## Audio Transcript", report)
            self.assertIn("# On-Screen Text", (tmp_path / "out" / "onscreen_text.md").read_text())

    @mock.patch(
        "content_ai_runtime.analyze._run_transcription",
        return_value=("hello world", [{"start": 0.0, "end": 1.0, "text": "hello world"}], "local", None),
    )
    @mock.patch("content_ai_runtime.analyze.acquire_video")
    @mock.patch("content_ai_runtime.analyze.detect_shots")
    @mock.patch("content_ai_runtime.analyze.extract_keyframes")
    @mock.patch("content_ai_runtime.analyze.create_contact_sheet")
    @mock.patch("content_ai_runtime.analyze.ffprobe_duration", return_value=1.0)
    @mock.patch("content_ai_runtime.analyze.resolve_ai_mode", return_value=("agent", None))
    @mock.patch("content_ai_runtime.analyze.run_codex_json")
    @mock.patch("content_ai_runtime.analyze.run_openai_vision_json")
    @mock.patch("content_ai_runtime.analyze.read_instagram_session", return_value=None)
    def test_agent_mode_writes_handoff_without_nested_llm_calls(
        self,
        _session,
        openai,
        codex,
        _ai,
        _duration,
        sheet,
        keyframes,
        shots,
        acquire,
        _transcribe,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "fixture.mp4"
            video.write_bytes(b"fake")
            keyframe = tmp_path / "frame.jpg"
            keyframe.write_bytes(b"fake")
            contact_sheet = tmp_path / "out" / "contact_sheet.jpg"
            sheet.return_value = contact_sheet
            acquire.return_value = (video, "local_file")
            shots.return_value = [{"shot_number": 1, "start_time": 0.0, "end_time": 1.0, "duration": 1.0}]
            keyframes.return_value = [keyframe]

            code, message = analyze_source("fixture.mp4", tmp_path / "out", ai="agent")

            self.assertEqual(code, 0)
            self.assertIn("agent handoff written", message)
            codex.assert_not_called()
            openai.assert_not_called()
            manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "needs_agent_synthesis")
            self.assertEqual(manifest["ai_mode"], "agent")
            self.assertFalse(manifest["capabilities"]["agent_synthesis"])
            self.assertTrue((tmp_path / "out" / "agent_request.md").exists())
            self.assertTrue((tmp_path / "out" / "agent_schema.json").exists())
            self.assertTrue((tmp_path / "out" / "agent_response.example.json").exists())
            request = (tmp_path / "out" / "agent_request.md").read_text()
            self.assertIn("Do not call `codex exec`", request)
            self.assertIn("agent_response.json", request)

    def test_apply_agent_response_updates_report_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "analysis"
            root.mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"source": "fixture.mp4", "status": "needs_agent_synthesis", "capabilities": {"transcription": True}}),
                encoding="utf-8",
            )
            (root / "reel.json").write_text(json.dumps({"source": "fixture.mp4", "duration": 1.0}), encoding="utf-8")
            (root / "media.json").write_text(json.dumps({"source": "fixture.mp4", "duration": 1.0, "instagram": {"status": "not_instagram_url"}}), encoding="utf-8")
            (root / "shots.json").write_text(json.dumps({"shots": [{"shot_number": 1, "start_time": 0.0, "end_time": 1.0, "duration": 1.0}]}), encoding="utf-8")
            (root / "transcript.md").write_text("# Audio Transcript\n\nhello world\n", encoding="utf-8")
            (root / "audience.md").write_text("# Audience\n\nNo comments fetched.\n", encoding="utf-8")
            (root / "agent_response.json").write_text(
                json.dumps({
                    "visual_summary": "visual from host agent",
                    "strategy_summary": "strategy from host agent",
                    "on_screen_text": ["caption"],
                    "ideas": [{"title": "Idea", "description": "Make it", "reasoning": "Evidence"}],
                }),
                encoding="utf-8",
            )

            code, message = apply_agent_response(root)

            self.assertEqual(code, 0)
            self.assertIn("agent response applied", message)
            manifest = json.loads((root / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["ai_mode"], "agent")
            self.assertIsNone(manifest.get("warning"))
            self.assertTrue(manifest["capabilities"]["agent_synthesis"])
            report = (root / "report.md").read_text()
            self.assertIn("visual from host agent", report)
            self.assertIn("- caption", report)
            self.assertIn("Idea", report)

    @mock.patch(
        "content_ai_runtime.analyze._run_transcription",
        return_value=("hello world", [{"start": 0.0, "end": 1.0, "text": "hello world"}], "api", None),
    )
    @mock.patch("content_ai_runtime.analyze.acquire_video")
    @mock.patch("content_ai_runtime.analyze.detect_shots")
    @mock.patch("content_ai_runtime.analyze.extract_keyframes", return_value=[])
    @mock.patch("content_ai_runtime.analyze.create_contact_sheet", return_value=None)
    @mock.patch("content_ai_runtime.analyze.ffprobe_duration", return_value=1.0)
    @mock.patch("content_ai_runtime.analyze.resolve_ai_mode", return_value=("api", None))
    @mock.patch("content_ai_runtime.analyze.run_openai_vision_json", return_value={})
    @mock.patch("content_ai_runtime.analyze.read_instagram_session", return_value="12345:secret")
    @mock.patch("content_ai_runtime.analyze.WebInstagramClient")
    def test_analyze_writes_instagram_audio_metadata_without_secret(
        self,
        client_cls,
        _session,
        _openai,
        _ai,
        _duration,
        _sheet,
        _keyframes,
        shots,
        acquire,
        _transcribe,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "fixture.mp4"
            video.write_bytes(b"fake")
            acquire.return_value = (video, "downloaded")
            shots.return_value = [{"shot_number": 1, "start_time": 0.0, "end_time": 1.0, "duration": 1.0}]
            client_cls.return_value.get_media_info.return_value = {
                "like_count": 7678,
                "comment_count": 542,
                "play_count": 123456,
                "taken_at": 1779683102,
                "user": {
                    "pk": 42,
                    "username": "creator",
                    "full_name": "Creator Name",
                    "is_verified": True,
                },
                "clips_metadata": {
                    "music_info": {
                        "music_asset_info": {
                            "id": "track-1",
                            "title": "Track Name",
                            "display_artist": "Artist Name",
                        }
                    }
                }
            }
            client_cls.return_value.get_user_profile.return_value = {
                "pk": "42",
                "username": "creator",
                "full_name": "Creator Name",
                "follower_count": 1234,
                "media_count": 56,
                "is_verified": True,
            }

            code, _message = analyze_source("https://www.instagram.com/reel/DXrUzP3DwWu/", tmp_path / "out", ai="api")

            self.assertEqual(code, 0)
            reel = json.loads((tmp_path / "out" / "reel.json").read_text())
            media = json.loads((tmp_path / "out" / "media.json").read_text())
            manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
            combined = json.dumps({"reel": reel, "media": media, "manifest": manifest})
            self.assertNotIn("12345:secret", combined)
            self.assertEqual(reel["instagram_audio"]["title"], "Track Name")
            self.assertEqual(reel["instagram_audio"]["artist"], "Artist Name")
            self.assertEqual(reel["instagram_creator"]["username"], "creator")
            self.assertEqual(reel["instagram_creator"]["follower_count"], 1234)
            self.assertEqual(reel["instagram_metrics"]["like_count"], 7678)
            self.assertEqual(reel["instagram_metrics"]["play_count"], 123456)
            self.assertEqual(media["instagram"]["audio"]["audio_asset_id"], "track-1")
            self.assertEqual(media["instagram"]["creator"]["username"], "creator")
            self.assertEqual(media["instagram"]["metrics"]["comment_count"], 542)
            report = (tmp_path / "out" / "report.md").read_text()
            self.assertIn("Creator: @creator", report)
            self.assertIn("1,234 followers", report)
            self.assertIn("Metrics: Plays: 123,456", report)
            self.assertIn("Likes: 7,678", report)
            self.assertTrue(manifest["capabilities"]["instagram_session_used"])
            self.assertTrue(manifest["capabilities"]["instagram_metadata"])
            self.assertTrue(manifest["capabilities"]["instagram_creator"])
            self.assertTrue(manifest["capabilities"]["instagram_metrics"])
