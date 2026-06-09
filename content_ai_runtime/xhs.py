from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import requests


XHS_METADATA_FILENAME = "xhs_note.json"
XHS_THUMBNAIL_FILENAME = "xhs_thumbnail.jpg"

NOTE_ID_RE = re.compile(
    r"(?:xiaohongshu\.com/(?:explore|discovery/item)|/explore/|/discovery/item/)([0-9a-f]{24})(?:[/?#]|$)",
    re.IGNORECASE,
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def is_xhs_url(value: str) -> bool:
    return bool(re.search(r"xiaohongshu\.com|xhslink\.com", value, re.IGNORECASE))


def extract_xhs_note_id(url: str) -> str | None:
    match = NOTE_ID_RE.search(str(url))
    return match.group(1).lower() if match else None


def _headers(accept: str = "*/*") -> dict[str, str]:
    return {
        "accept": accept,
        "referer": "https://www.xiaohongshu.com/",
        "user-agent": UA,
    }


def parse_initial_state(html: str) -> dict[str, Any]:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{[\s\S]*?\})\s*</script>", html)
    if not match:
        raise RuntimeError("XHS initial state was not found on the note page")
    raw = re.sub(r"\bundefined\b", "null", match.group(1))
    return json.loads(raw)


def _note_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("noteId") or value.get("note_id") or value.get("id") or "").lower()


def _looks_like_note(value: Any) -> bool:
    return isinstance(value, dict) and (
        "video" in value
        or "imageList" in value
        or "image_list" in value
        or "desc" in value
        or "interactInfo" in value
    )


def find_note_in_state(state: dict[str, Any], expected_id: str | None) -> dict[str, Any]:
    maps = [
        state.get("note", {}).get("noteDetailMap") if isinstance(state.get("note"), dict) else None,
        state.get("noteData", {}).get("noteDetailMap") if isinstance(state.get("noteData"), dict) else None,
        state.get("feed", {}).get("noteDetailMap") if isinstance(state.get("feed"), dict) else None,
        state.get("explore", {}).get("noteDetailMap") if isinstance(state.get("explore"), dict) else None,
    ]
    for note_map in [m for m in maps if isinstance(m, dict)]:
        for key, value in note_map.items():
            note = value.get("note") if isinstance(value, dict) and isinstance(value.get("note"), dict) else value
            note_id = _note_id(note) or str(key).lower()
            if _looks_like_note(note) and (not expected_id or note_id == expected_id or expected_id in str(key).lower()):
                return note

    stack: list[Any] = [state]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if _looks_like_note(value) and (not expected_id or _note_id(value) == expected_id):
                return value
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    raise RuntimeError("XHS note payload was not found in initial state")


def _first(values: Iterable[Any]) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def image_urls(note: dict[str, Any]) -> list[str]:
    images = note.get("imageList") or note.get("image_list") or note.get("imagesList") or []
    urls: list[str] = []
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, dict):
            continue
        candidates: list[Any] = [image.get("urlDefault"), image.get("urlPre"), image.get("url")]
        for key in ("infoList", "info_list"):
            info_list = image.get(key)
            if isinstance(info_list, list):
                candidates.extend(item.get("url") for item in info_list if isinstance(item, dict))
        first = _first(candidates)
        if first:
            urls.append(first)
    return list(dict.fromkeys(urls))


def video_urls(note: dict[str, Any]) -> list[str]:
    stream = (((note.get("video") or {}).get("media") or {}).get("stream") or {})
    urls: list[str] = []
    if isinstance(stream, dict):
        for bucket in ("h264", "h265", "av1"):
            items = stream.get(bucket) or []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                for key in ("masterUrl", "master_url"):
                    if item.get(key):
                        urls.append(str(item[key]))
                for key in ("backupUrls", "backup_urls"):
                    backups = item.get(key)
                    if isinstance(backups, list):
                        urls.extend(str(url) for url in backups if url)
    return list(dict.fromkeys(urls))


def thumbnail_url(note: dict[str, Any]) -> str | None:
    video = note.get("video") or {}
    video_image = video.get("image") if isinstance(video, dict) else {}
    if isinstance(video_image, dict):
        thumb = _first([video_image.get("urlDefault"), video_image.get("urlPre"), video_image.get("url")])
        if thumb:
            return thumb
    if isinstance(video, dict):
        thumb = _first([video.get("coverUrl"), video.get("cover_url")])
        if thumb:
            return thumb
    images = image_urls(note)
    return images[0] if images else None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def note_metadata(note: dict[str, Any], *, note_id: str | None, source_url: str) -> dict[str, Any]:
    user = note.get("user") or {}
    interact = note.get("interactInfo") or note.get("interact_info") or {}
    video = note.get("video") or {}
    media = video.get("media") if isinstance(video, dict) else {}
    return {
        "status": "fetched",
        "note_id": note_id or _note_id(note),
        "title": str(note.get("title") or note.get("displayTitle") or note.get("display_title") or "").strip(),
        "desc": str(note.get("desc") or note.get("description") or "").strip(),
        "type": str(note.get("type") or "").strip(),
        "source_url": source_url,
        "author": {
            "id": str(user.get("userId") or user.get("user_id") or user.get("id") or "").strip() if isinstance(user, dict) else "",
            "nickname": str(user.get("nickname") or user.get("name") or "").strip() if isinstance(user, dict) else "",
        },
        "stats": {
            "likes": _safe_count(interact.get("likedCount") or interact.get("liked_count")) if isinstance(interact, dict) else None,
            "collections": _safe_count(interact.get("collectedCount") or interact.get("collected_count")) if isinstance(interact, dict) else None,
            "comments": _safe_count(interact.get("commentCount") or interact.get("comment_count")) if isinstance(interact, dict) else None,
            "shares": _safe_count(interact.get("shareCount") or interact.get("share_count")) if isinstance(interact, dict) else None,
        },
        "duration": _safe_float(
            ((media or {}).get("videoDuration") or (media or {}).get("duration") or video.get("duration"))
            if isinstance(video, dict)
            else None
        ),
        "thumbnail_url": thumbnail_url(note),
        "image_urls": image_urls(note),
        "video_urls": video_urls(note),
    }


def caption_from_metadata(metadata: dict[str, Any]) -> str:
    title = (metadata.get("title") or "").strip()
    desc = (metadata.get("desc") or "").strip()
    if title and desc and title not in desc:
        return f"{title}\n\n{desc}"
    return desc or title or metadata.get("note_id") or "Xiaohongshu note"


def download_xhs_video(source: str, output_dir: Path) -> Path:
    page = requests.get(
        source,
        headers=_headers("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        timeout=30,
        allow_redirects=True,
    )
    page.raise_for_status()
    final_url = str(page.url or source)
    note_id = extract_xhs_note_id(source) or extract_xhs_note_id(final_url)
    note = find_note_in_state(parse_initial_state(page.text), note_id)
    metadata = note_metadata(note, note_id=note_id, source_url=final_url)
    videos = metadata.get("video_urls") or []
    if not videos:
        raise RuntimeError("XHS note did not expose a downloadable video URL")

    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "source.mp4"
    with requests.get(videos[0], headers=_headers(), stream=True, timeout=120) as response:
        response.raise_for_status()
        with video_path.open("wb") as file_obj:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    file_obj.write(chunk)

    thumb_url = metadata.get("thumbnail_url")
    if thumb_url:
        try:
            thumb_response = requests.get(thumb_url, headers=_headers(), timeout=30)
            thumb_response.raise_for_status()
            if thumb_response.content:
                thumb_path = output_dir / XHS_THUMBNAIL_FILENAME
                thumb_path.write_bytes(thumb_response.content)
                metadata["local_thumbnail"] = thumb_path.name
        except requests.RequestException:
            metadata["thumbnail_download_error"] = "thumbnail download failed"

    metadata["caption"] = caption_from_metadata(metadata)
    (output_dir / XHS_METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return video_path
