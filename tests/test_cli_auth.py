"""Contracts for the provider alias table shared by `arity auth login` and `arity auth logout`."""
from __future__ import annotations

import argparse
import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

from arity.cli import PROVIDER_ALIASES, handle_auth_command

LOGIN_FUNCTIONS = (
    "login_google_antigravity",
    "login_openai_codex",
    "login_xai_grok",
    "login_anthropic",
)


def _run_auth(**namespace):
    """Returns (exit code, TokenStore instance, {login name: mock})."""
    store = MagicMock()
    logins = {name: MagicMock() for name in LOGIN_FUNCTIONS}
    with contextlib.ExitStack() as stack:
        # handle_auth_command imports from .auth inside the function, so the patches
        # must land on arity.auth rather than on arity.cli.
        stack.enter_context(patch("arity.auth.TokenStore", return_value=store))
        for name, fn in logins.items():
            stack.enter_context(patch(f"arity.auth.{name}", fn))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        code = handle_auth_command(argparse.Namespace(**namespace))
    return code, store, logins


class TestAuthProviderAliases(unittest.TestCase):
    def test_logout_removes_the_key_the_credential_is_stored_under(self):
        code, store, _ = _run_auth(auth_action="logout", provider="claude")
        self.assertEqual(code, 0)
        store.delete_credential.assert_called_once_with("anthropic")

    def test_every_alias_reaches_exactly_one_login_backend(self):
        self.assertTrue(PROVIDER_ALIASES)
        for alias in PROVIDER_ALIASES:
            with self.subTest(alias=alias):
                code, _, logins = _run_auth(auth_action="login", provider=alias)
                self.assertEqual(code, 0)
                called = [name for name, fn in logins.items() if fn.call_count]
                self.assertEqual(len(called), 1, f"{alias} dispatched to {called}")

    def test_unknown_names_are_rejected_on_login_and_passed_through_on_logout(self):
        code, _, logins = _run_auth(auth_action="login", provider="nosuchprovider")
        self.assertEqual(code, 2)
        self.assertFalse([name for name, fn in logins.items() if fn.call_count])

        qualified = "google-antigravity:someone@example.com"
        code, store, _ = _run_auth(auth_action="logout", provider=qualified)
        self.assertEqual(code, 0)
        store.delete_credential.assert_called_once_with(qualified)


if __name__ == "__main__":
    unittest.main()
