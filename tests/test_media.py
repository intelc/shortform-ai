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

    @mock.patch("content_ai_runtime.xhs.requests.get")
    def test_acquire_video_downloads_xhs_note_video_thumbnail_and_metadata(self, get):
        note = {
            "noteId": "69aa97da000000001d01204a",
            "title": "Launch notes",
            "desc": "The note body matters.",
            "type": "video",
            "user": {"userId": "u1", "nickname": "maker"},
            "interactInfo": {"likedCount": "1.2万", "commentCount": "34"},
            "video": {
                "image": {"urlDefault": "https://sns-img.example/cover.jpg"},
                "media": {
                    "videoDuration": 12.5,
                    "stream": {
                        "h264": [{"masterUrl": "https://sns-video.example/video.mp4"}],
                    },
                },
            },
        }
        state = {"note": {"noteDetailMap": {"69aa97da000000001d01204a": {"note": note}}}}
        html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"

        class Response:
            def __init__(self, *, text="", content=b"", url="https://www.xiaohongshu.com/explore/69aa97da000000001d01204a"):
                self.text = text
                self.content = content
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield self.content

        get.side_effect = [
            Response(text=html),
            Response(content=b"fake-mp4"),
            Response(content=b"fake-jpg"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            video, acquisition = media.acquire_video(
                "https://www.xiaohongshu.com/explore/69aa97da000000001d01204a?xsec_token=abc",
                out,
            )

            self.assertEqual(acquisition, "downloaded_xhs")
            self.assertEqual(video.read_bytes(), b"fake-mp4")
            self.assertEqual((out / "xhs_thumbnail.jpg").read_bytes(), b"fake-jpg")
            metadata = json.loads((out / "xhs_note.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["caption"], "Launch notes\n\nThe note body matters.")
            self.assertEqual(metadata["stats"]["likes"], 12000)
            self.assertEqual(metadata["video_urls"], ["https://sns-video.example/video.mp4"])
