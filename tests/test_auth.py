"""Unit tests for gorkbot auth module (TokenStore, PKCE, token refresh, and auto-import)."""
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from gorkbot.auth import (
    AuthConfigurationError,
    TokenStore,
    generate_pkce_pair,
    login_google_antigravity,
    refresh_google_antigravity_token,
    refresh_openai_token,
    refresh_xai_token,
)


class TestGorkbotAuth(unittest.TestCase):
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

        configured = {
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID": "test-google-client-id",
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET": "test-google-client-secret",
        }
        with patch.dict(os.environ, configured), patch(
            "urllib.request.urlopen",
            return_value=mock_resp,
        ):
            res = refresh_google_antigravity_token(
                refresh_token="old_refresh",
                project_id="proj-456",
            )
            self.assertEqual(res["access"], "fresh_access_token")
            self.assertEqual(res["refresh"], "new_refresh")
            self.assertEqual(res["projectId"], "proj-456")
            self.assertGreater(res["expires"], 0)

    def test_google_refresh_prefers_explicit_client_configuration(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_access_token", "expires_in": 3600}'
        mock_resp.__enter__.return_value = mock_resp
        configured = {
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID": "environment-client-id",
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET": "environment-client-secret",
        }

        with patch.dict(os.environ, configured), patch(
            "urllib.request.urlopen",
            return_value=mock_resp,
        ) as mocked_urlopen:
            refresh_google_antigravity_token(
                refresh_token="test-refresh",
                project_id="test-project",
                client_id="explicit-client-id",
                client_secret="explicit-client-secret",
            )

        request = mocked_urlopen.call_args.args[0]
        payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(payload["client_id"], ["explicit-client-id"])
        self.assertEqual(payload["client_secret"], ["explicit-client-secret"])

    def test_google_refresh_requires_configuration_before_network(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "urllib.request.urlopen"
        ) as mocked_urlopen:
            with self.assertRaises(AuthConfigurationError) as raised:
                refresh_google_antigravity_token(
                    refresh_token="test-refresh",
                    project_id="test-project",
                )

        self.assertIn("ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID", str(raised.exception))
        self.assertIn("ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET", str(raised.exception))
        mocked_urlopen.assert_not_called()

    def test_google_login_requires_configuration_before_side_effects(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "http.server.HTTPServer"
        ) as mocked_server, patch("webbrowser.open") as mocked_browser, patch(
            "urllib.request.urlopen"
        ) as mocked_urlopen:
            with self.assertRaises(AuthConfigurationError):
                login_google_antigravity()

        mocked_server.assert_not_called()
        mocked_browser.assert_not_called()
        mocked_urlopen.assert_not_called()

    def test_google_refresh_uses_imported_client_configuration(self):
        self.store.save_credential(
            "google-antigravity:test-account",
            {
                "access": "expired-test-access",
                "refresh": "test-refresh",
                "expires": 1,
                "projectId": "test-project",
                "clientId": "imported-client-id",
                "clientSecret": "imported-client-secret",
            },
        )
        refreshed = {
            "access": "fresh-test-access",
            "refresh": "test-refresh",
            "expires": 1789000000000,
            "projectId": "test-project",
        }

        with patch(
            "gorkbot.auth.refresh_google_antigravity_token",
            return_value=refreshed,
        ) as mocked_refresh:
            result = self.store.refresh_if_needed("google-antigravity:test-account")

        self.assertEqual(result["access"], "fresh-test-access")
        mocked_refresh.assert_called_once_with(
            refresh_token="test-refresh",
            project_id="test-project",
            client_id="imported-client-id",
            client_secret="imported-client-secret",
        )

    def test_refresh_openai_token(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_codex_token", "expires_in": 3600}'
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict(
            os.environ,
            {"ARITY_OPENAI_CLIENT_ID": "test-openai-client-id"},
        ), patch("urllib.request.urlopen", return_value=mock_resp):
            res = refresh_openai_token(refresh_token="old_codex_refresh")
            self.assertEqual(res["access"], "fresh_codex_token")
            self.assertEqual(res["refresh"], "old_codex_refresh")

    def test_refresh_xai_token(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"access_token": "fresh_xai_token", "expires_in": 3600}'
        mock_resp.__enter__.return_value = mock_resp

        with patch.dict(
            os.environ,
            {"ARITY_XAI_CLIENT_ID": "test-xai-client-id"},
        ), patch("urllib.request.urlopen", return_value=mock_resp):
            res = refresh_xai_token(refresh_token="old_xai_refresh")
            self.assertEqual(res["access"], "fresh_xai_token")


if __name__ == "__main__":
    unittest.main()
