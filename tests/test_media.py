from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from content_ai_runtime import media


class MediaTests(TestCase):
    @mock.patch("content_ai_runtime.media.shutil.which", return_value=None)
    def test_command_path_finds_venv_adjacent_script(self, _which):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            python = bin_dir / "python"
            python.write_text("", encoding="utf-8")
            tool = bin_dir / "yt-dlp"
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(tool.stat().st_mode | 0o111)

            with mock.patch("content_ai_runtime.media.sys.executable", str(python)):
                self.assertEqual(Path(media.command_path("yt-dlp")).resolve(), tool.resolve())
                self.assertTrue(media.have_command("yt-dlp"))

    @mock.patch("content_ai_runtime.media.command_path", return_value="/usr/bin/ffprobe")
    @mock.patch("content_ai_runtime.media.subprocess.run")
    def test_ffprobe_duration_reads_duration(self, run, _command_path):
        run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps({"format": {"duration": "3.25"}}),
        )

        self.assertEqual(media.ffprobe_duration(Path("video.mp4")), 3.25)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/ffprobe")

    @mock.patch("content_ai_runtime.media.command_path", return_value=None)
    def test_ffprobe_duration_without_ffprobe_is_none(self, _command_path):
        self.assertIsNone(media.ffprobe_duration(Path("video.mp4")))

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_scene_threshold_default_is_more_sensitive(self):
        self.assertEqual(media.scene_threshold(), 24.0)

    @mock.patch.dict("os.environ", {"SHORTFORM_AI_SCENE_THRESHOLD": "18.5"}, clear=True)
    def test_scene_threshold_can_be_overridden(self):
        self.assertEqual(media.scene_threshold(), 18.5)

    @mock.patch("content_ai_runtime.media.command_path", return_value="/usr/bin/ffmpeg")
    @mock.patch("content_ai_runtime.media.subprocess.run")
    def test_create_contact_sheet_uses_ffmpeg_tile(self, run, _command_path):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "contact_sheet.jpg"

            def fake_run(args, **_kwargs):
                out.write_bytes(b"jpg")
                return mock.Mock(returncode=0)

            run.side_effect = fake_run

            result = media.create_contact_sheet(Path("video.mp4"), out, duration=9.0)

            self.assertEqual(result, out)
            command = run.call_args.args[0]
            self.assertIn("-vf", command)
            self.assertIn("tile=3x3", command[command.index("-vf") + 1])
