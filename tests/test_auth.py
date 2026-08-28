"""Unit tests for arity auth module (TokenStore, PKCE, token refresh, and auto-import)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from arity.auth import (
    TokenStore,
    generate_pkce_pair,
    refresh_google_antigravity_token,
    refresh_openai_token,
    refresh_xai_token,
)


class TestArityAuth(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.auth_file = Path(self.tmp_dir.name) / "auth.json"
        self.store = TokenStore(auth_path=self.auth_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_pkce_generation(self):
        verifier, challenge = generate_pkce_pair()
        self.assertGreater(len(verifier), 40)
        self.assertGreater(len(challenge), 40)
        self.assertNotIn("=", challenge)  # URL-safe base64 unpadded

    def test_save_and_load_credential(self):
        data = {
            "access": "test_access_token",
            "refresh": "test_refresh_token",
            "projectId": "test-project-123",
            "expires": 1789000000000,
        }
        self.store.save_credential("google-antigravity", data)
        loaded = self.store.load_all()
        self.assertIn("google-antigravity", loaded)
        self.assertEqual(loaded["google-antigravity"]["projectId"], "test-project-123")

        got = self.store.get_credential("google-antigravity")
        self.assertIsNotNone(got)
        self.assertEqual(got["access"], "test_access_token")

    def test_delete_credential(self):
        self.store.save_credential("mock-prov", {"access": "abc"})
        self.assertTrue(self.store.delete_credential("mock-prov"))
        self.assertFalse(self.store.delete_credential("mock-prov"))
        self.assertNotIn("mock-prov", self.store.load_all())

    def test_refresh_google_antigravity_token(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_access_token", "expires_in": 3600, "refresh_token": "new_refresh"}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = refresh_google_antigravity_token(refresh_token="old_refresh", project_id="proj-456")
            self.assertEqual(res["access"], "fresh_access_token")
            self.assertEqual(res["refresh"], "new_refresh")
            self.assertEqual(res["projectId"], "proj-456")
            self.assertGreater(res["expires"], 0)

    def test_refresh_openai_token(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_codex_token", "expires_in": 3600}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = refresh_openai_token(refresh_token="old_codex_refresh")
            self.assertEqual(res["access"], "fresh_codex_token")
            self.assertEqual(res["refresh"], "old_codex_refresh")

    def test_refresh_xai_token(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_xai_token", "expires_in": 3600}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = refresh_xai_token(refresh_token="old_xai_refresh")
            self.assertEqual(res["access"], "fresh_xai_token")


if __name__ == "__main__":
    unittest.main()
