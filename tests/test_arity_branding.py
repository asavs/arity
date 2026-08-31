"""Focused contracts for Arity branding and arity resolution."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arity import __version__
from arity.cli import main
from arity.orchestrator import ArityOrchestrator
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

    def test_precedence_is_explicit_then_arity_then_default(self):
        with patch("arity.tools.get_config_value") as configured:
            self.assertEqual(resolve_arity(7, default=1), 7)
            configured.assert_not_called()

        with patch("arity.tools.get_config_value", return_value="5"):
            self.assertEqual(resolve_arity(default=1), 5)

        with patch("arity.tools.get_config_value", return_value=None):
            self.assertEqual(resolve_arity(default=3), 3)

    def test_invalid_current_setting_is_rejected(self):
        with patch("arity.tools.get_config_value", return_value="many"):
            with self.assertRaisesRegex(ValueError, "ARITY must be a positive integer"):
                resolve_arity(default=1)

    def test_front_door_uses_resolved_arity_in_mock_mode(self):
        report = SimpleNamespace(
            judgements=[], conference_winner=None, winner=None,
            candidates=[object(), object()], notes=[], requested_arity=None,
        )
        delivery = SimpleNamespace(asked_human=False, receipt="done")
        with (
            patch.dict(os.environ, {"ARITY": "2"}, clear=False),
            patch("arity.race.run_race", return_value=report) as run_race,
            patch("arity.race.deliver", return_value=delivery),
        ):
            run_front_door("brief", mock=True, interactive=False)
        config = run_race.call_args.args[0]
        self.assertEqual(config.workers, 2)
        self.assertEqual(len(config.variants.split(",")), 2)
        self.assertEqual(config.requested_arity, 2)
        self.assertEqual(report.requested_arity, 2)
        self.assertEqual(report.notes, [])

    def test_mock_arity_is_a_transparent_maximum(self):
        report = SimpleNamespace(
            judgements=[], conference_winner=None, winner=None,
            candidates=[object(), object(), object()], notes=[], requested_arity=None,
        )
        delivery = SimpleNamespace(asked_human=False, receipt="done")
        with (
            patch("arity.race.run_race", return_value=report) as run_race,
            patch("arity.race.deliver", return_value=delivery),
        ):
            run_front_door("brief", candidates=5, mock=True, interactive=False)
        config = run_race.call_args.args[0]
        self.assertEqual(config.workers, 3)
        self.assertEqual(len(config.variants.split(",")), 3)
        self.assertEqual(config.requested_arity, 5)
        self.assertEqual(report.requested_arity, 5)
        self.assertEqual(report.notes[0], "arity requested max 5; resolved 3 unique candidates")
        self.assertTrue(delivery.receipt.startswith("arity 3/5 resolved"))


class TestArityBranding(unittest.TestCase):
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
        self.assertIn(f"Arity {__version__}", output.getvalue())
        self.assertIn("Python API: import arity", output.getvalue())

        run_help = io.StringIO()
        with patch.object(sys, "argv", ["arity", "run", "--help"]), contextlib.redirect_stdout(run_help):
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 0)
        normalized_help = " ".join(run_help.getvalue().split())
        self.assertIn("Positive maximum candidate count", normalized_help)
        self.assertIn("may resolve fewer unique seats", normalized_help)

    def test_cli_rejects_non_positive_arity(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["arity", "run", "--arity", "0"]), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("positive integer", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
