"""Focused contracts for Arity branding, arity resolution, and compatibility."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arity.cli import main
from arity.orchestrator import ArityOrchestrator, ArityOrchestrator
from arity.race import run_front_door
from arity.roles import RoleRegistry
from arity.spirals import render_brand_mark
from arity.tools import positive_int, resolve_arity


class TestArityResolution(unittest.TestCase):
    def test_positive_integer_contract(self):
        self.assertEqual(positive_int(3, name="arity"), 3)
        self.assertEqual(positive_int("+4", name="ARITY"), 4)
        for invalid in (0, -1, "0", "-2", "2.5", 2.5, True, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "positive integer"):
                positive_int(invalid, name="arity")

    def test_precedence_is_explicit_then_arity_then_legacy_then_default(self):
        with patch("arity.tools.get_config_value") as configured:
            self.assertEqual(resolve_arity(7, default=1), 7)
            configured.assert_not_called()

        values = {"ARITY": "5", "ARITY_CONCURRENCY": "2"}
        with patch("arity.tools.get_config_value", side_effect=values.get):
            self.assertEqual(resolve_arity(default=1), 5)

        values = {"ARITY": None, "ARITY_CONCURRENCY": "2"}
        with patch("arity.tools.get_config_value", side_effect=values.get):
            self.assertEqual(resolve_arity(default=1), 2)

        with patch("arity.tools.get_config_value", return_value=None):
            self.assertEqual(resolve_arity(default=3), 3)

    def test_invalid_current_setting_does_not_fall_through_to_legacy(self):
        values = {"ARITY": "many", "ARITY_CONCURRENCY": "2"}
        with patch("arity.tools.get_config_value", side_effect=values.get):
            with self.assertRaisesRegex(ValueError, "ARITY must be a positive integer"):
                resolve_arity(default=1)

    def test_front_door_uses_resolved_arity_in_mock_mode(self):
        report = SimpleNamespace(judgements=[], conference_winner=None, winner=None)
        delivery = SimpleNamespace(asked_human=False)
        with (
            patch.dict(os.environ, {"ARITY": "2", "ARITY_CONCURRENCY": "1"}, clear=False),
            patch("arity.race.run_race", return_value=report) as run_race,
            patch("arity.race.deliver", return_value=delivery),
        ):
            run_front_door("brief", mock=True, interactive=False)
        config = run_race.call_args.args[0]
        self.assertEqual(config.workers, 2)
        self.assertEqual(len(config.variants.split(",")), 2)


class TestArityBranding(unittest.TestCase):
    def test_current_and_compatibility_orchestrator_names_share_one_class(self):
        self.assertIs(ArityOrchestrator, ArityOrchestrator)

    def test_role_listing_deduplicates_aliases_without_hashing_roles(self):
        roles = RoleRegistry().list_roles()
        self.assertTrue(roles)
        self.assertEqual(len(roles), len({id(role) for role in roles}))

    def test_brand_mark_is_the_cli_mark(self):
        mark = render_brand_mark(width=23, height=9, seeds=55)
        self.assertEqual(len(mark.splitlines()), 10)
        self.assertIn("@", mark)
        self.assertIn("Arity | one task, N agents, facts first", mark)

        output = io.StringIO()
        with patch.object(sys, "argv", ["arity", "--help"]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("Arity 0.3.0", output.getvalue())
        self.assertIn("Compatibility: the arity command alias", output.getvalue())

    def test_cli_rejects_non_positive_arity(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["arity", "run", "--arity", "0"]), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("positive integer", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
