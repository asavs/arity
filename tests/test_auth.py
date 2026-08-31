"""Unit tests for gorkbot auth module (TokenStore, PKCE, token refresh, and auto-import)."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

from gorkbot.auth import (
    AuthConfigurationError,
    TokenStore,
    generate_pkce_pair,
    login_anthropic,
    login_google_antigravity,
    login_openai_codex,
    login_xai_grok,
    refresh_google_antigravity_token,
    refresh_openai_token,
    refresh_xai_token,
)
from gorkbot.cli import main as cli_main


def _json_response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    return response


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _loopback_server(callback_path):
    class SyntheticLoopbackServer:
        def __init__(self, _address, handler_class):
            self.handler_class = handler_class

        def serve_forever(self):
            handler = object.__new__(self.handler_class)
            handler.path = (
                f"{callback_path}?code=synthetic-code&state=synthetic-state"
            )
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()
            handler.do_GET()

        def shutdown(self):
            pass

        def server_close(self):
            pass

    return SyntheticLoopbackServer


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

    def test_save_atomically_replaces_file_and_cleans_temporary_file(self):
        real_replace = os.replace
        with patch("gorkbot.auth.os.replace", wraps=real_replace) as mocked_replace:
            self.store.save_credential("mock-provider", {"access": "test-access"})

        mocked_replace.assert_called_once()
        self.assertEqual(self.store.load_all()["mock-provider"]["access"], "test-access")
        self.assertEqual(list(self.auth_file.parent.glob(".auth.json.*.tmp")), [])

    def test_failed_atomic_replace_preserves_existing_credentials(self):
        self.store.save_credential("first-provider", {"access": "first-test-access"})
        original = self.auth_file.read_bytes()

        with patch("gorkbot.auth.os.replace", side_effect=OSError("test replace failure")):
            with self.assertRaises(OSError):
                self.store.save_credential(
                    "second-provider",
                    {"access": "second-test-access"},
                )

        self.assertEqual(self.auth_file.read_bytes(), original)
        self.assertEqual(list(self.auth_file.parent.glob(".auth.json.*.tmp")), [])

    def test_cleanup_failure_does_not_mask_replace_failure(self):
        with patch(
            "gorkbot.auth.os.replace",
            side_effect=OSError("synthetic replace failure"),
        ), patch.object(
            Path,
            "unlink",
            side_effect=OSError("synthetic cleanup failure"),
        ):
            with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                self.store.save_credential(
                    "mock-provider",
                    {"access": "synthetic-access"},
                )

    @unittest.skipUnless(os.name == "posix", "POSIX file mode semantics")
    def test_saved_credentials_are_owner_only_on_posix(self):
        self.store.save_credential("mock-provider", {"access": "test-access"})
        self.assertEqual(stat.S_IMODE(self.auth_file.stat().st_mode), 0o600)

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

    def test_refresh_preserves_existing_token_when_provider_returns_empty_value(self):
        cases = (
            (
                "google-null",
                b'{"access_token": "fresh-access", "refresh_token": null}',
                lambda: refresh_google_antigravity_token(
                    refresh_token="existing-refresh",
                    project_id="synthetic-project",
                    client_id="synthetic-google-client",
                    client_secret="synthetic-google-secret",
                ),
            ),
            (
                "openai-empty",
                b'{"access_token": "fresh-access", "refresh_token": ""}',
                lambda: refresh_openai_token(
                    refresh_token="existing-refresh",
                    client_id="synthetic-openai-client",
                ),
            ),
            (
                "xai-null",
                b'{"access_token": "fresh-access", "refresh_token": null}',
                lambda: refresh_xai_token(
                    refresh_token="existing-refresh",
                    client_id="synthetic-xai-client",
                ),
            ),
        )
        for provider, body, refresh in cases:
            with self.subTest(provider=provider):
                response = MagicMock()
                response.read.return_value = body
                response.__enter__.return_value = response
                with patch("urllib.request.urlopen", return_value=response):
                    result = refresh()

                self.assertEqual(result["refresh"], "existing-refresh")

    def test_google_login_configuration_survives_into_refresh(self):
        missing_configuration = {
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID": "",
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET": "",
        }
        configured = {
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID": "synthetic-google-client",
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET": "synthetic-google-secret",
        }
        token_response = _json_response(
            {
                "access_token": "synthetic-google-access",
                "refresh_token": "synthetic-google-refresh",
                "expires_in": 0,
            }
        )
        user_response = _json_response({})

        with patch.dict(os.environ, missing_configuration):
            with patch.dict(os.environ, configured), patch(
                "gorkbot.auth.generate_pkce_pair",
                return_value=("synthetic-verifier", "synthetic-challenge"),
            ), patch(
                "gorkbot.auth.secrets.token_urlsafe",
                return_value="synthetic-state",
            ), patch(
                "gorkbot.auth.http.server.HTTPServer",
                _loopback_server("/oauth-callback"),
            ), patch(
                "gorkbot.auth.threading.Thread",
                _ImmediateThread,
            ), patch(
                "gorkbot.auth.urllib.request.urlopen",
                side_effect=[token_response, user_response],
            ), patch(
                "gorkbot.auth.discover_and_onboard_antigravity_project",
                return_value="synthetic-project",
            ), patch(
                "gorkbot.auth.TokenStore",
                return_value=self.store,
            ):
                login_google_antigravity(open_browser=False)

            saved = self.store.get_credential("google-antigravity")
            self.assertEqual(saved["clientId"], "synthetic-google-client")
            self.assertEqual(saved["clientSecret"], "synthetic-google-secret")

            refreshed = {
                "access": "fresh-google-access",
                "refresh": "synthetic-google-refresh",
                "expires": 1789000000000,
                "projectId": "synthetic-project",
            }
            with patch(
                "gorkbot.auth.refresh_google_antigravity_token",
                return_value=refreshed,
            ) as mocked_refresh:
                result = self.store.refresh_if_needed("google-antigravity")

        self.assertEqual(result["access"], "fresh-google-access")
        mocked_refresh.assert_called_once_with(
            refresh_token="synthetic-google-refresh",
            project_id="synthetic-project",
            client_id="synthetic-google-client",
            client_secret="synthetic-google-secret",
        )

    def test_openai_login_configuration_survives_into_refresh(self):
        token_response = _json_response(
            {
                "access_token": "synthetic-openai-access",
                "refresh_token": "synthetic-openai-refresh",
                "expires_in": 0,
            }
        )
        with patch.dict(os.environ, {"ARITY_OPENAI_CLIENT_ID": ""}):
            with patch.dict(
                os.environ,
                {"ARITY_OPENAI_CLIENT_ID": "synthetic-openai-client"},
            ), patch(
                "gorkbot.auth.generate_pkce_pair",
                return_value=("synthetic-verifier", "synthetic-challenge"),
            ), patch(
                "gorkbot.auth.secrets.token_urlsafe",
                return_value="synthetic-state",
            ), patch(
                "gorkbot.auth.http.server.HTTPServer",
                _loopback_server("/callback"),
            ), patch(
                "gorkbot.auth.threading.Thread",
                _ImmediateThread,
            ), patch(
                "gorkbot.auth.urllib.request.urlopen",
                return_value=token_response,
            ), patch(
                "gorkbot.auth.TokenStore",
                return_value=self.store,
            ):
                login_openai_codex(open_browser=False)

            saved = self.store.get_credential("openai-codex")
            self.assertEqual(saved["clientId"], "synthetic-openai-client")
            refreshed = {
                "access": "fresh-openai-access",
                "refresh": "synthetic-openai-refresh",
                "expires": 1789000000000,
            }
            with patch(
                "gorkbot.auth.refresh_openai_token",
                return_value=refreshed,
            ) as mocked_refresh:
                result = self.store.refresh_if_needed("openai-codex")

        self.assertEqual(result["access"], "fresh-openai-access")
        mocked_refresh.assert_called_once_with(
            refresh_token="synthetic-openai-refresh",
            client_id="synthetic-openai-client",
        )

    def test_xai_login_configuration_survives_into_refresh(self):
        device_response = _json_response(
            {
                "device_code": "synthetic-device-code",
                "user_code": "synthetic-user-code",
                "verification_uri": "https://example.invalid/device",
                "interval": 0,
            }
        )
        token_response = _json_response(
            {
                "access_token": "synthetic-xai-access",
                "refresh_token": "synthetic-xai-refresh",
                "expires_in": 0,
            }
        )
        with patch.dict(os.environ, {"ARITY_XAI_CLIENT_ID": ""}):
            with patch.dict(
                os.environ,
                {"ARITY_XAI_CLIENT_ID": "synthetic-xai-client"},
            ), patch(
                "gorkbot.auth.urllib.request.urlopen",
                side_effect=[device_response, token_response],
            ), patch(
                "gorkbot.auth.time.sleep",
            ), patch(
                "gorkbot.auth.TokenStore",
                return_value=self.store,
            ):
                login_xai_grok(open_browser=False)

            saved = self.store.get_credential("xai-oauth")
            self.assertEqual(saved["clientId"], "synthetic-xai-client")
            refreshed = {
                "access": "fresh-xai-access",
                "refresh": "synthetic-xai-refresh",
                "expires": 1789000000000,
            }
            with patch(
                "gorkbot.auth.refresh_xai_token",
                return_value=refreshed,
            ) as mocked_refresh:
                result = self.store.refresh_if_needed("xai-oauth")

        self.assertEqual(result["access"], "fresh-xai-access")
        mocked_refresh.assert_called_once_with(
            refresh_token="synthetic-xai-refresh",
            client_id="synthetic-xai-client",
        )

    def test_anthropic_login_persists_resolved_client_configuration(self):
        token_response = _json_response(
            {
                "access_token": "synthetic-anthropic-access",
                "refresh_token": "synthetic-anthropic-refresh",
                "expires_in": 0,
                "account": {},
            }
        )
        with patch.dict(
            os.environ,
            {"ARITY_ANTHROPIC_CLIENT_ID": "synthetic-anthropic-client"},
        ), patch(
            "gorkbot.auth.generate_pkce_pair",
            return_value=("synthetic-verifier", "synthetic-challenge"),
        ), patch(
            "gorkbot.auth.secrets.token_urlsafe",
            return_value="synthetic-state",
        ), patch(
            "gorkbot.auth.http.server.HTTPServer",
            _loopback_server("/callback"),
        ), patch(
            "gorkbot.auth.threading.Thread",
            _ImmediateThread,
        ), patch(
            "gorkbot.auth.urllib.request.urlopen",
            return_value=token_response,
        ), patch(
            "gorkbot.auth.TokenStore",
            return_value=self.store,
        ):
            login_anthropic(open_browser=False)

        saved = self.store.get_credential("anthropic")
        self.assertEqual(saved["clientId"], "synthetic-anthropic-client")


class TestAuthCli(unittest.TestCase):
    def test_login_missing_configuration_is_side_effect_free(self):
        missing_configuration = {
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID": "",
            "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET": "",
            "ARITY_OPENAI_CLIENT_ID": "",
            "ARITY_XAI_CLIENT_ID": "",
            "ARITY_ANTHROPIC_CLIENT_ID": "",
        }
        required_environment = {
            "google": "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID",
            "openai": "ARITY_OPENAI_CLIENT_ID",
            "xai": "ARITY_XAI_CLIENT_ID",
            "anthropic": "ARITY_ANTHROPIC_CLIENT_ID",
        }
        for provider, environment_name in required_environment.items():
            with self.subTest(provider=provider):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with patch.dict(os.environ, missing_configuration), patch.object(
                    sys,
                    "argv",
                    ["arity", "auth", "login", provider],
                ), patch("http.server.HTTPServer") as mocked_server, patch(
                    "webbrowser.open"
                ) as mocked_browser, patch(
                    "urllib.request.urlopen"
                ) as mocked_urlopen, contextlib.redirect_stdout(
                    stdout
                ), contextlib.redirect_stderr(
                    stderr
                ):
                    return_code = cli_main()

                self.assertEqual(return_code, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("[Arity auth]", stderr.getvalue())
                self.assertIn(environment_name, stderr.getvalue())
                self.assertNotIn("auth import", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())
                mocked_server.assert_not_called()
                mocked_browser.assert_not_called()
                mocked_urlopen.assert_not_called()

    def test_successful_login_returns_zero_through_main(self):
        with patch.object(
            sys,
            "argv",
            ["arity", "auth", "login", "google"],
        ), patch(
            "gorkbot.auth.login_google_antigravity",
            return_value={"access": "synthetic-access"},
        ) as mocked_login:
            return_code = cli_main()

        self.assertEqual(return_code, 0)
        mocked_login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
