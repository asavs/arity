"""Tests for Arity sandbox tool runner, AST validation, and MCP adapter."""
from __future__ import annotations

import unittest
import socket
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arity.research import ResearchScope
from arity.roles import BUILDER_ROLE, JUDGE_ROLE, Role, RoleRegistry
from arity.tools import (
    McpToolAdapter,
    PathTraversalError,
    SandboxToolRunner,
    create_mcp_tool_runner,
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
        # Judge role is denied write_file
        judge_runner = SandboxToolRunner(workspace_root=self.ws, role=JUDGE_ROLE)
        res = judge_runner.execute(
            ExecuteTool(
                call_id="call_judge_write",
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

    def test_roleful_mcp_runner_cannot_self_grant_web_research(self):
        override = Role(
            name="developer",
            description="Untrusted override",
            allowed_tools=("web_search",),
            trusted_definition=False,
        )
        roleful = create_mcp_tool_runner(workspace_root=self.ws, role=override)
        roleful_names = {
            schema["function"]["name"] for schema in roleful.get_schemas()
        }
        self.assertNotIn("web_search", roleful_names)

        denied = roleful.execute(
            ExecuteTool(
                call_id="mcp_role_web",
                name="web_search",
                arguments={"query": "must not leave the host boundary"},
            )
        )
        self.assertTrue(denied.is_error)

        role_free = create_mcp_tool_runner(workspace_root=self.ws)
        role_free_names = {
            schema["function"]["name"] for schema in role_free.get_schemas()
        }
        self.assertIn("web_search", role_free_names)

    def test_queried_fetch_never_forwards_url_to_reader_proxy(self):
        opened_urls: list[str] = []

        class EmptyPublicResponse:
            headers = {"Content-Type": "text/html"}

            def __init__(self, url: str) -> None:
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return self.url

            def read(self, *_args, **_kwargs) -> bytes:
                return b"<html><body>empty</body></html>"

        class RecordingOpener:
            def open(self, request, *_args, **_kwargs):
                opened_urls.append(str(request.full_url))
                return EmptyPublicResponse(str(request.full_url))

        registry = RoleRegistry()
        runner = SandboxToolRunner(
            workspace_root=self.ws / "scout-child",
            role=registry.get("scout"),
            role_registry=registry,
            research_scope=ResearchScope(project_root=self.ws),
        )
        secret = "QUERY_PROXY_SECRET"
        public_answer = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]
        with (
            patch("socket.getaddrinfo", return_value=public_answer),
            patch("urllib.request.build_opener", return_value=RecordingOpener()),
        ):
            result = runner.execute(
                ExecuteTool(
                    call_id="query_origin_only",
                    name="fetch_url",
                    arguments={
                        "url": f"https://public.example/docs?token={secret}"
                    },
                )
            )

        self.assertTrue(result.is_error)
        self.assertEqual(len(opened_urls), 1)
        self.assertTrue(opened_urls[0].startswith("https://public.example/"))
        self.assertNotIn("r.jina.ai", opened_urls[0])
        self.assertNotIn(secret, result.output)

    def test_roleful_research_scope_requires_trusted_research_role(self):
        scope = ResearchScope(project_root=self.ws)
        denied_roles = (
            Role(
                name="scout",
                description="Untrusted override",
                research_access=True,
                trusted_definition=False,
            ),
            Role(
                name="scout",
                description="Trusted but not research enabled",
                research_access=False,
            ),
            Role(
                name="designer",
                description="Trusted wrong personality",
                research_access=True,
            ),
        )
        for role in denied_roles:
            with self.subTest(role=role.name, description=role.description):
                with self.assertRaisesRegex(ValueError, "trusted|research|grant"):
                    SandboxToolRunner(
                        workspace_root=self.ws / f"denied-{role.name}",
                        role=role,
                        research_scope=scope,
                    )

        trusted_scout = Role(
            name="scout",
            description="Host-authorized Scout",
            research_access=True,
        )
        roleful = SandboxToolRunner(
            workspace_root=self.ws / "trusted-scout",
            role=trusted_scout,
            research_scope=scope,
        )
        self.assertIn(
            "read_project_file",
            {schema["function"]["name"] for schema in roleful.get_schemas()},
        )

        host_runner = SandboxToolRunner(
            workspace_root=self.ws / "host-seam",
            role=None,
            research_scope=scope,
        )
        self.assertIn(
            "read_project_file",
            {schema["function"]["name"] for schema in host_runner.get_schemas()},
        )

    def test_queried_fetch_blocks_cross_origin_redirect_before_request(self):
        requested_urls: list[str] = []
        secret = "QUERY_REDIRECT_SECRET"

        class RedirectingOpener:
            def __init__(self, handlers: tuple[object, ...]) -> None:
                self.handlers = handlers

            def open(self, request, *_args, **_kwargs):
                requested_urls.append(str(request.full_url))
                redirect_handler = next(
                    handler
                    for handler in self.handlers
                    if isinstance(handler, urllib.request.HTTPRedirectHandler)
                )
                redirect_handler.redirect_request(
                    request,
                    fp=None,
                    code=302,
                    msg="Found",
                    headers={},
                    newurl=f"https://other.example/collect?token={secret}",
                )
                raise AssertionError("cross-origin redirect should fail before request")

        registry = RoleRegistry()
        runner = SandboxToolRunner(
            workspace_root=self.ws / "redirect-scout",
            role=registry.get("scout"),
            role_registry=registry,
            research_scope=ResearchScope(project_root=self.ws),
        )
        public_answer = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]
        with (
            patch("socket.getaddrinfo", return_value=public_answer),
            patch(
                "urllib.request.build_opener",
                side_effect=lambda *handlers: RedirectingOpener(handlers),
            ),
        ):
            result = runner.execute(
                ExecuteTool(
                    call_id="query_redirect_origin",
                    name="fetch_url",
                    arguments={
                        "url": f"https://public.example/docs?token={secret}"
                    },
                )
            )

        self.assertTrue(result.is_error)
        self.assertEqual(len(requested_urls), 1)
        self.assertTrue(requested_urls[0].startswith("https://public.example/"))
        self.assertNotIn("other.example", requested_urls[0])
        self.assertNotIn(secret, result.output)


if __name__ == "__main__":
    unittest.main()
