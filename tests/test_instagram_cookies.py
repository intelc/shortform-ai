from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import TestCase

from content_ai_runtime.instagram_cookies import read_instagram_sessions_from_files


class InstagramCookieTests(TestCase):
    def test_reads_plaintext_session_from_cookie_db(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cookie_db = Path(tmp) / "Cookies"
            con = sqlite3.connect(cookie_db)
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
            con.execute(
                "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
                (".instagram.com", "sessionid", "12345:plain-session", b""),
            )
            con.commit()
            con.close()

            sessions, err = read_instagram_sessions_from_files(
                files=[str(cookie_db)],
                decrypt_value=lambda _blob: None,
                use_temp_copy=False,
            )

            self.assertIsNone(err)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].user_id, "12345")
            self.assertEqual(sessions[0].sessionid, "12345:plain-session")

    def test_reads_encrypted_session_via_decrypt_callback(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cookie_db = Path(tmp) / "Cookies"
            con = sqlite3.connect(cookie_db)
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
            con.execute(
                "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
                (".instagram.com", "sessionid", "", b"encrypted"),
            )
            con.commit()
            con.close()

            sessions, err = read_instagram_sessions_from_files(
                files=[str(cookie_db)],
                decrypt_value=lambda _blob: "67890:decrypted-session",
                use_temp_copy=False,
            )

            self.assertIsNone(err)
            self.assertEqual(sessions[0].user_id, "67890")
