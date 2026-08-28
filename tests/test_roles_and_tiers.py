"""Tests for gorkbot roles, denial sets, and memory tier brief compilation."""
from __future__ import annotations

import unittest

from gorkbot.roles import (
    ARCHITECT_ROLE,
    BUILDER_ROLE,
    REVIEWER_ROLE,
    VOICE_ROLE,
    DenialSet,
    Role,
    RoleRegistry,
)
from gorkbot.tiers import (
    BriefCompiler,
    BriefRefusalError,
    PredecessorAccounts,
    TierLevel,
    compute_identity,
)


class TestRolesAndDenialSets(unittest.TestCase):
    def setUp(self):
        self.registry = RoleRegistry()

    def test_role_resolution(self):
        voice = self.registry.resolve("voice")
        self.assertEqual(voice.name, "secretary")

        # Semantic resolution
        builder = self.registry.resolve("implement a new database schema")
        self.assertEqual(builder.name, "python_developer")
        reviewer = self.registry.resolve("audit code and check PR")
        self.assertEqual(reviewer.name, "reviewer")
    def test_denial_set_tool_enforcement(self):
        # Reviewer is denied write_file
        self.assertFalse(REVIEWER_ROLE.can_use_tool("write_file"))
        self.assertTrue(REVIEWER_ROLE.can_use_tool("read_file"))

        # Builder can use write_file, read_file, run_command
        self.assertTrue(BUILDER_ROLE.can_use_tool("write_file"))
        self.assertTrue(BUILDER_ROLE.can_use_tool("read_file"))
        self.assertTrue(BUILDER_ROLE.can_use_tool("run_command"))

    def test_denial_set_path_enforcement(self):
        # Builder is denied .ssh and .env
        self.assertFalse(BUILDER_ROLE.can_access_path("C:/Users/example/.ssh/id_rsa"))
        self.assertFalse(BUILDER_ROLE.can_access_path(".env"))
        self.assertTrue(BUILDER_ROLE.can_access_path("src/main.py"))

    def test_filter_tools(self):
        tools = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ]
        filtered_reviewer = self.registry.filter_tools(REVIEWER_ROLE, tools)
        self.assertEqual(len(filtered_reviewer), 1)
        self.assertEqual(filtered_reviewer[0]["function"]["name"], "read_file")

class TestTiersAndBriefCompiler(unittest.TestCase):
    def setUp(self):
        self.compiler = BriefCompiler(
            tier0_context="Asa: High-context personal notes and biograph.",
            tier1_context="gorkbot: Five seams architecture.",
        )

    def test_memory_tier_isolation(self):
        # Secretary gets personal context
        voice_brief = self.compiler.assemble(VOICE_ROLE, "Hello Asa")
        self.assertIn("Personal Context", voice_brief.system_prompt)
        self.assertIn("Asa: High-context personal notes", voice_brief.system_prompt)

        # Python Developer DOES NOT get personal context (Axiom 8)
        builder_brief = self.compiler.assemble(BUILDER_ROLE, "Build the scraper")
        self.assertNotIn("Personal Context", builder_brief.system_prompt)
        self.assertNotIn("Asa: High-context personal notes", builder_brief.system_prompt)
        self.assertIn("Operational Scope (python_developer)", builder_brief.system_prompt)
    def test_predecessor_accounts_included(self):
        predecessor = PredecessorAccounts(
            self_report="I built the deal schema in brokie/schema.sql",
            archivist_entry="Verified schema.sql created with 1 table.",
        )
        brief = self.compiler.assemble(
            BUILDER_ROLE,
            "Add a new table",
            predecessor=predecessor,
        )
        self.assertIn("Predecessor Self-Report", brief.system_prompt)
        self.assertIn("Archivist Audit Entry", brief.system_prompt)
        self.assertIn("I built the deal schema", brief.system_prompt)

    def test_brief_refusal_on_denial_set_violation(self):
        from gorkbot.roles import DenialSet, Role
        restricted_role = Role(
            name="leaf_worker",
            description="Sandboxed worker",
            tier=3,
            denial_set=DenialSet(denied_names=("secret_client_corp", "internal_classified_project")),
        )
        # Attempting to compile a brief leaking a denied entity name triggers BriefRefusalError (Axiom 8 DLP)
        with self.assertRaises(BriefRefusalError):
            self.compiler.assemble(
                restricted_role,
                "Please research secret_client_corp data",
            )

        # But ordinary coding prompts mentioning .env or ssh assemble cleanly without crashing
        brief = self.compiler.assemble(
            BUILDER_ROLE,
            "Write a config loader that reads .env files and loads settings",
        )
        self.assertIsNotNone(brief)
        self.assertIn(".env", brief.user_prompt)
    def test_identity_tuple_computation(self):
        identity = compute_identity(
            provider="openai",
            endpoint="https://api.openai.com/v1",
            model="gpt-4o",
            workspace="ws_1",
            session_id="sess_123",
            brief_hash="abc12345",
        )
        self.assertTrue(identity.startswith("openai:gpt-4o:sess_123:"))


if __name__ == "__main__":
    unittest.main()
