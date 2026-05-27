from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
IG_APP_ID = "936619743392459"
API_BASE = "https://www.instagram.com/api/v1"

_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_URL_RE = re.compile(r"instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_\-]+)")


class WebInstagramClientError(RuntimeError):
    pass


def shortcode_to_media_id(shortcode: str) -> int | None:
    try:
        media_id = 0
        for char in shortcode:
            media_id = media_id * 64 + _SHORTCODE_ALPHABET.index(char)
        return media_id
    except ValueError:
        return None


def extract_shortcode_from_url(url: str) -> str | None:
    match = _URL_RE.search(url)
    return match.group(1) if match else None


def _iso_timestamp(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("username") or value.get("full_name") or value.get("name")
    text = str(value).strip()
    return text or None


def _first_string(*values: Any) -> str | None:
    for value in values:
        text = _clean_string(value)
        if text:
            return text
    return None


def _first_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


class WebInstagramClient:
    """Small web-API client for an explicitly imported local Instagram session."""

    def __init__(self, *, sessionid: str, min_interval_seconds: float = 0.35) -> None:
        if not sessionid:
            raise WebInstagramClientError("No Instagram session provided.")
        self._cookies = {"sessionid": urllib.parse.unquote(sessionid)}
        if ":" in self._cookies["sessionid"]:
            user_id = self._cookies["sessionid"].split(":", 1)[0]
            if user_id.isdigit():
                self._cookies["ds_user_id"] = user_id
        self._last_request_at = 0.0
        self._min_interval_seconds = min_interval_seconds
        self._session = requests.Session()
        self._session.headers.update(self._default_headers())

    def _default_headers(self) -> dict[str, str]:
        cookie_header = "; ".join(f"{key}={value}" for key, value in self._cookies.items())
        return {
            "Cookie": cookie_header,
            "X-CSRFToken": self._cookies.get("csrftoken", ""),
            "X-IG-App-ID": IG_APP_ID,
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _acquire(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_seconds:
            time.sleep(self._min_interval_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._acquire()
        response = self._session.get(f"{API_BASE}{path}", params=params, timeout=20)
        body_preview = response.text[:500] if response.text else ""
        suspicious = any(
            marker in body_preview.lower()
            for marker in (
                "checkpoint_url",
                "challenge_required",
                "unusual",
                "suspicious",
                "accounts/login",
                "/challenge/",
            )
        )
        if suspicious or response.status_code in (400, 302):
            raise WebInstagramClientError(
                f"Instagram checkpoint/login signal on {path} (HTTP {response.status_code}). "
                "Log back into Instagram in Chrome, then re-run auth import."
            )
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            time.sleep(min(retry_after, 300))
            self._acquire()
            response = self._session.get(f"{API_BASE}{path}", params=params, timeout=20)
        if response.status_code != 200:
            raise WebInstagramClientError(
                f"Instagram API {path} returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise WebInstagramClientError(
                f"Instagram API {path} returned non-JSON response: {response.text[:200]}"
            ) from exc

    def get_media_comments(self, media_id: str, count: int = 50) -> list[dict[str, Any]]:
        headers = {
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        raw_comments: list[dict[str, Any]] = []

        try:
            self._acquire()
            response = self._session.get(
                f"{API_BASE}/media/{media_id}/comments/",
                params={"can_support_threading": "true", "permalink_enabled": "false"},
                headers=headers,
                timeout=20,
            )
            if response.status_code == 200 and "application/json" in response.headers.get("content-type", ""):
                raw_comments = response.json().get("comments") or []
        except Exception:
            raw_comments = []

        if not raw_comments:
            info = self._get_json(f"/media/{media_id}/info/")
            items = info.get("items") or []
            if items:
                raw_comments = items[0].get("preview_comments") or []

        comments: list[dict[str, Any]] = []
        for comment in raw_comments[:count]:
            user = comment.get("user") or {}
            comments.append({
                "text": (comment.get("text") or "").strip(),
                "username": user.get("username", ""),
                "like_count": int(comment.get("comment_like_count") or 0),
                "created_at": comment.get("created_at"),
                "created_at_iso": _iso_timestamp(comment.get("created_at")),
                "is_verified": bool(user.get("is_verified")),
            })
        comments.sort(key=lambda item: item["like_count"], reverse=True)
        return comments

    def get_media_info(self, media_id: str) -> dict[str, Any]:
        data = self._get_json(f"/media/{media_id}/info/")
        items = data.get("items") or []
        if not items:
            raise WebInstagramClientError(f"media {media_id} not found")
        item = items[0]
        if not isinstance(item, dict):
            raise WebInstagramClientError(f"media {media_id} returned an unexpected shape")
        return item

    def get_user_profile(self, username: str) -> dict[str, Any]:
        data = self._get_json("/users/web_profile_info/", params={"username": username})
        user = (data.get("data") or {}).get("user")
        if not user:
            raise WebInstagramClientError(f"profile @{username} not found")
        return {
            "pk": str(user.get("id") or ""),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "biography": user.get("biography"),
            "follower_count": (user.get("edge_followed_by") or {}).get("count"),
            "following_count": (user.get("edge_follow") or {}).get("count"),
            "media_count": (user.get("edge_owner_to_timeline_media") or {}).get("count"),
            "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
            "is_verified": user.get("is_verified"),
            "is_private": user.get("is_private"),
            "external_url": user.get("external_url"),
            "bio_links": user.get("bio_links") or [],
        }

    def get_user_feed_page(
        self,
        user_id: str,
        *,
        count: int = 12,
        max_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"count": count}
        if max_id:
            params["max_id"] = max_id
        data = self._get_json(f"/feed/user/{user_id}/", params=params)
        next_max = data.get("next_max_id") if data.get("more_available") else None
        return data.get("items") or [], (str(next_max) if next_max else None)


def summarize_feed_item(item: dict[str, Any]) -> dict[str, Any]:
    caption = (item.get("caption") or {}).get("text") or ""
    image_versions = item.get("image_versions2") or {}
    candidates = image_versions.get("candidates") or []
    video_versions = item.get("video_versions") or []
    return {
        "pk": str(item.get("pk") or item.get("id") or ""),
        "code": item.get("code"),
        "media_type": item.get("media_type"),
        "product_type": item.get("product_type"),
        "taken_at": item.get("taken_at"),
        "taken_at_iso": _iso_timestamp(item.get("taken_at")),
        "caption": caption,
        "like_count": item.get("like_count"),
        "comment_count": item.get("comment_count"),
        "play_count": item.get("play_count"),
        "view_count": item.get("view_count"),
        "thumbnail_url": candidates[0].get("url") if candidates else None,
        "video_url_present": bool(video_versions),
    }


def extract_instagram_creator_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Return safe, non-secret creator identity fields from an Instagram media item."""
    user = item.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    pk = _first_string(user.get("pk"), user.get("id"))
    return {
        "pk": pk,
        "username": _first_string(user.get("username")),
        "full_name": _first_string(user.get("full_name")),
        "is_verified": _first_bool(user.get("is_verified")),
        "is_private": _first_bool(user.get("is_private")),
        "profile_pic_url": _first_string(user.get("profile_pic_url"), user.get("profile_pic_url_hd")),
    }


def extract_instagram_metrics(item: dict[str, Any]) -> dict[str, Any]:
    """Return safe aggregate engagement/performance fields from an Instagram media item."""
    clips = item.get("clips_metadata") or {}
    return {
        "like_count": _first_int(item.get("like_count"), item.get("like_count_str")),
        "comment_count": _first_int(item.get("comment_count"), item.get("comments_count")),
        "play_count": _first_int(
            item.get("play_count"),
            item.get("ig_play_count"),
            item.get("fb_play_count"),
            clips.get("play_count"),
        ),
        "view_count": _first_int(
            item.get("view_count"),
            item.get("video_view_count"),
            item.get("view_count_str"),
            clips.get("view_count"),
        ),
        "reshare_count": _first_int(item.get("reshare_count"), item.get("share_count")),
        "save_count": _first_int(item.get("save_count"), item.get("saved_count")),
        "taken_at": _first_int(item.get("taken_at")),
        "taken_at_iso": _iso_timestamp(item.get("taken_at")),
    }


def extract_instagram_audio_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Return safe, non-secret audio attribution from an Instagram media item."""
    clips = item.get("clips_metadata") or {}
    music_info = clips.get("music_info") or {}
    music_asset = music_info.get("music_asset_info") or {}
    original_sound = clips.get("original_sound_info") or {}
    attribution = item.get("clips_music_attribution_info") or clips.get("clips_music_attribution_info") or {}

    raw_fields_present = []
    if music_info:
        raw_fields_present.append("clips_metadata.music_info")
    if original_sound:
        raw_fields_present.append("clips_metadata.original_sound_info")
    if attribution:
        raw_fields_present.append("clips_music_attribution_info")

    title = _first_string(
        music_asset.get("title"),
        music_asset.get("song_name"),
        attribution.get("song_name"),
        attribution.get("title"),
        original_sound.get("original_audio_title"),
        original_sound.get("audio_title"),
        original_sound.get("title"),
    )
    artist = _first_string(
        music_asset.get("display_artist"),
        music_asset.get("artist_name"),
        music_asset.get("subtitle"),
        attribution.get("artist_name"),
        attribution.get("artist"),
        original_sound.get("ig_artist"),
        original_sound.get("artist_name"),
        original_sound.get("artist"),
    )
    audio_asset_id = _first_string(
        music_asset.get("id"),
        music_asset.get("audio_asset_id"),
        original_sound.get("audio_asset_id"),
        original_sound.get("music_canonical_id"),
        attribution.get("audio_asset_id"),
        attribution.get("music_canonical_id"),
    )

    is_original_audio = _first_bool(
        original_sound.get("is_original_audio"),
        music_asset.get("is_original_audio"),
        attribution.get("uses_original_audio"),
    )
    if is_original_audio is None and original_sound:
        is_original_audio = True

    if music_info:
        audio_type = "licensed_music"
    elif original_sound:
        audio_type = "original_audio"
    elif attribution:
        audio_type = "attributed_audio"
    else:
        audio_type = "unknown"

    return {
        "audio_type": audio_type,
        "title": title,
        "artist": artist,
        "audio_asset_id": audio_asset_id,
        "is_original_audio": is_original_audio,
        "duration_in_ms": _first_int(
            music_asset.get("duration_in_ms"),
            music_asset.get("duration_ms"),
            original_sound.get("duration_in_ms"),
            attribution.get("duration_in_ms"),
        ),
        "should_mute_audio": _first_bool(
            music_info.get("should_mute_audio"),
            original_sound.get("should_mute_audio"),
            attribution.get("should_mute_audio"),
        ),
        "raw_fields_present": raw_fields_present,
    }
