"""Tests for Arity sandbox tool runner, AST validation, and MCP adapter."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arity.roles import BUILDER_ROLE, REVIEWER_ROLE
from arity.tools import (
    McpToolAdapter,
    PathTraversalError,
    SandboxToolRunner,
    resolve_sandbox_path,
)
from arity.types import ExecuteTool


class TestSandboxToolRunner(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        self.runner = SandboxToolRunner(workspace_root=self.ws, role=BUILDER_ROLE)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_path_confinement_prevents_escape(self):
        # Valid path inside workspace
        target = resolve_sandbox_path(self.ws, "src/main.py")
        self.assertTrue(str(target).startswith(str(self.ws.resolve())))

        # Escaping path with ../ must raise PathTraversalError
        with self.assertRaises(PathTraversalError):
            resolve_sandbox_path(self.ws, "../../../Windows/System32/cmd.exe")

        # ExecuteTool calling read_file on escaping path returns security error
        res = self.runner.execute(
            ExecuteTool(
                call_id="call_esc",
                name="read_file",
                arguments={"path": "../../../secret.txt"},
            )
        )
        self.assertTrue(res.is_error)
        self.assertIn("Security Error", res.output)

    def test_ast_syntax_check_blocks_corrupt_python(self):
        # Attempting to write invalid Python
        res = self.runner.execute(
            ExecuteTool(
                call_id="call_bad_py",
                name="write_file",
                arguments={"path": "bad.py", "content": "def broken_func(:\n  return 1"},
            )
        )
        self.assertTrue(res.is_error)
        self.assertIn("Syntax Error", res.output)

        # File should NOT exist on disk
        self.assertFalse((self.ws / "bad.py").exists())

        # Valid Python writes successfully
        res_ok = self.runner.execute(
            ExecuteTool(
                call_id="call_good_py",
                name="write_file",
                arguments={"path": "good.py", "content": "def working_func():\n  return 1\n"},
            )
        )
        self.assertFalse(res_ok.is_error)
        self.assertTrue((self.ws / "good.py").exists())

    def test_role_denial_enforcement_in_runner(self):
        # Reviewer role is denied write_file
        reviewer_runner = SandboxToolRunner(workspace_root=self.ws, role=REVIEWER_ROLE)
        res = reviewer_runner.execute(
            ExecuteTool(
                call_id="call_rev_write",
                name="write_file",
                arguments={"path": "test.txt", "content": "hello"},
            )
        )
        self.assertTrue(res.is_error)
        self.assertIn("Security Denial", res.output)

        # Builder role is denied .ssh paths
        res_denied_path = self.runner.execute(
            ExecuteTool(
                call_id="call_ssh",
                name="read_file",
                arguments={"path": ".ssh/id_rsa"},
            )
        )
        self.assertTrue(res_denied_path.is_error)
        self.assertIn("denied access to path", res_denied_path.output)

    def test_mcp_adapter_schema_and_execution(self):
        adapter = McpToolAdapter(
            mcp_client_callable=lambda name, args: f"Executed {name} with query={args.get('q')}"
        )
        adapter.register_mcp_tool({
            "name": "web_search",
            "description": "Search the web",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        })

        schemas = adapter.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "web_search")

        res = adapter.execute(
            ExecuteTool(
                call_id="mcp_call",
                name="web_search",
                arguments={"q": "statechart agents"},
            )
        )
        self.assertFalse(res.is_error)
        self.assertEqual(res.output, "Executed web_search with query=statechart agents")


if __name__ == "__main__":
    unittest.main()
