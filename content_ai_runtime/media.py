from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def command_path(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found

    executable_dir = Path(sys.executable).resolve().parent
    candidates = [executable_dir / name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        candidates.append(executable_dir / f"{name}.exe")
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def have_command(name: str) -> bool:
    return command_path(name) is not None


def ffprobe_duration(video_path: Path) -> float | None:
    ffprobe = command_path("ffprobe")
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except Exception:
        return None


def has_audio_stream(video_path: Path) -> bool:
    ffprobe = command_path("ffprobe")
    if not ffprobe:
        return False
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False
    try:
        return bool(json.loads(proc.stdout).get("streams"))
    except Exception:
        return False


def extract_audio(video_path: Path, audio_path: Path) -> bool:
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        return False
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-y",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0


def acquire_video(source: str, output_dir: Path) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if is_url(source):
        yt_dlp = command_path("yt-dlp")
        if not yt_dlp:
            raise RuntimeError("yt-dlp is required to download URLs. Install yt-dlp or pass a local file.")
        target = output_dir / "source.mp4"
        proc = subprocess.run(
            [
                yt_dlp,
                "-f",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "-o",
                str(target),
                source,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError("video download failed")
        return target, "downloaded"

    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"input file not found: {path}")
    target = output_dir / f"source{path.suffix or '.mp4'}"
    if path != target:
        shutil.copy2(path, target)
    return target, "local_file"


def detect_shots(video_path: Path) -> list[dict]:
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector
    except ImportError:
        duration = ffprobe_duration(video_path) or 0.0
        return [
            {
                "shot_number": 1,
                "start_time": 0.0,
                "end_time": duration,
                "duration": duration,
                "source": "fallback_single_shot",
            }
        ]

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=30.0))
    manager.detect_scenes(video)
    scenes = manager.get_scene_list()
    shots = []
    for index, (start, end) in enumerate(scenes, 1):
        shots.append(
            {
                "shot_number": index,
                "start_time": start.get_seconds(),
                "end_time": end.get_seconds(),
                "duration": (end - start).get_seconds(),
                "start_frame": start.get_frames(),
                "end_frame": end.get_frames(),
                "source": "scenedetect",
            }
        )
    if shots:
        return shots
    duration = ffprobe_duration(video_path) or 0.0
    return [
        {
            "shot_number": 1,
            "start_time": 0.0,
            "end_time": duration,
            "duration": duration,
            "source": "fallback_single_shot",
        }
    ]


def extract_keyframes(video_path: Path, shots: list[dict], keyframes_dir: Path) -> list[Path]:
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        return []
    paths: list[Path] = []
    for shot in shots:
        midpoint = (float(shot["start_time"]) + float(shot["end_time"])) / 2.0
        out = keyframes_dir / f"shot_{int(shot['shot_number']):03d}.jpg"
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{midpoint:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-y",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and out.exists():
            paths.append(out)
    return paths


def create_contact_sheet(
    video_path: Path,
    output_path: Path,
    *,
    duration: float | None = None,
    max_frames: int = 9,
    columns: int = 3,
) -> Path | None:
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = duration if duration is not None else ffprobe_duration(video_path)
    interval = 1.0
    if duration and duration > 0:
        interval = max(duration / max(max_frames, 1), 1.0)
    rows = max(1, math.ceil(max_frames / max(columns, 1)))
    vf = (
        f"fps=1/{interval:.3f},"
        "scale=320:-1,"
        f"tile={columns}x{rows}:padding=8:margin=8:color=black"
    )
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-y",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    return None
