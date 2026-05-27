from __future__ import annotations

from unittest import TestCase

from content_ai_runtime.instagram_client import (
    WebInstagramClient,
    extract_instagram_audio_metadata,
    extract_instagram_creator_metadata,
    extract_instagram_metrics,
    extract_shortcode_from_url,
    shortcode_to_media_id,
    summarize_feed_item,
)


class InstagramClientTests(TestCase):
    def test_extract_shortcode_from_reel_url(self):
        self.assertEqual(
            extract_shortcode_from_url("https://www.instagram.com/reel/DWkQ7wbip37/?igsh=abc"),
            "DWkQ7wbip37",
        )

    def test_shortcode_to_media_id_rejects_invalid_character(self):
        self.assertIsNone(shortcode_to_media_id("not valid"))

    def test_summarize_feed_item_keeps_safe_public_fields(self):
        item = {
            "pk": 123,
            "code": "ABC",
            "media_type": 2,
            "product_type": "clips",
            "caption": {"text": "hello"},
            "like_count": 5,
            "comment_count": 2,
            "video_versions": [{"url": "https://cdn.example/video.mp4"}],
            "image_versions2": {"candidates": [{"url": "https://cdn.example/thumb.jpg"}]},
        }

        summary = summarize_feed_item(item)

        self.assertEqual(summary["pk"], "123")
        self.assertEqual(summary["code"], "ABC")
        self.assertEqual(summary["caption"], "hello")
        self.assertTrue(summary["video_url_present"])

    def test_get_media_info_unwraps_first_item(self):
        client = WebInstagramClient.__new__(WebInstagramClient)
        client._get_json = lambda _path: {"items": [{"pk": "111", "code": "ABC"}]}

        self.assertEqual(client.get_media_info("111"), {"pk": "111", "code": "ABC"})

    def test_extract_instagram_creator_metadata_keeps_safe_fields(self):
        metadata = extract_instagram_creator_metadata({
            "user": {
                "pk": 42,
                "username": "creator",
                "full_name": "Creator Name",
                "is_verified": True,
                "profile_pic_url": "https://cdn.example/profile.jpg",
            }
        })

        self.assertEqual(metadata["pk"], "42")
        self.assertEqual(metadata["username"], "creator")
        self.assertEqual(metadata["full_name"], "Creator Name")
        self.assertTrue(metadata["is_verified"])

    def test_extract_instagram_metrics_keeps_engagement_counts(self):
        metrics = extract_instagram_metrics({
            "like_count": 7678,
            "comment_count": 542,
            "play_count": 123456,
            "taken_at": 1779683102,
        })

        self.assertEqual(metrics["like_count"], 7678)
        self.assertEqual(metrics["comment_count"], 542)
        self.assertEqual(metrics["play_count"], 123456)
        self.assertEqual(metrics["taken_at_iso"], "2026-05-25T04:25:02+00:00")

    def test_extract_instagram_audio_metadata_for_licensed_music(self):
        metadata = extract_instagram_audio_metadata({
            "clips_metadata": {
                "music_info": {
                    "music_asset_info": {
                        "id": "audio-1",
                        "title": "A Named Track",
                        "display_artist": "An Artist",
                        "duration_in_ms": 30200,
                    },
                    "should_mute_audio": False,
                }
            }
        })

        self.assertEqual(metadata["audio_type"], "licensed_music")
        self.assertEqual(metadata["title"], "A Named Track")
        self.assertEqual(metadata["artist"], "An Artist")
        self.assertEqual(metadata["audio_asset_id"], "audio-1")
        self.assertFalse(metadata["should_mute_audio"])
        self.assertIn("clips_metadata.music_info", metadata["raw_fields_present"])

    def test_extract_instagram_audio_metadata_for_original_audio(self):
        metadata = extract_instagram_audio_metadata({
            "clips_metadata": {
                "original_sound_info": {
                    "audio_asset_id": "original-1",
                    "original_audio_title": "Original audio",
                    "ig_artist": {"username": "creator"},
                }
            }
        })

        self.assertEqual(metadata["audio_type"], "original_audio")
        self.assertEqual(metadata["title"], "Original audio")
        self.assertEqual(metadata["artist"], "creator")
        self.assertEqual(metadata["audio_asset_id"], "original-1")
        self.assertTrue(metadata["is_original_audio"])
