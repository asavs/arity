"""arity tools — Sandbox tool runner, AST syntax validation, and MCP adapter.

Axiom 2: Roles are denial sets (tools, paths, hosts).
Axiom 12: Tool runners are pluggable seams (MCP, native Rust, local sandbox).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .roles import Role
from .seams import ToolRunner
from .types import ExecuteTool, ToolCompleted


class PathTraversalError(Exception):
    """Raised when a path escapes the allowed workspace boundary."""
    pass


class SyntaxValidationError(Exception):
    """Raised when written code fails syntax parsing."""
    pass


def resolve_sandbox_path(workspace_root: Path, relative_path: str) -> Path:
    """Resolve and enforce that the path stays strictly inside workspace_root."""
    resolved_root = workspace_root.resolve()
    target = (workspace_root / relative_path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        raise PathTraversalError(f"Path '{relative_path}' escapes workspace boundary '{workspace_root}'")
    return target


class SandboxToolRunner(ToolRunner):
    """Enforces workspace path confinement, AST syntax checks, and role denial sets."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        role: Optional[Role] = None,
        timeout: int = 30,
    ):
        self.workspace_root = (Path(workspace_root) if workspace_root else Path.cwd()).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.role = role
        self.timeout = timeout
        self._custom_tools: dict[str, Callable[..., str]] = {}
        self._schemas: list[dict[str, Any]] = []
        self._register_default_tools()

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., str],
    ) -> None:
        """Register a custom tool function with JSON schema."""
        self._custom_tools[name] = func
        self._schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas filtered by role permissions if role is assigned."""
        if not self.role:
            return list(self._schemas)
        filtered = []
        for s in self._schemas:
            fn_name = s.get("function", {}).get("name", "")
            if self.role.can_use_tool(fn_name):
                filtered.append(s)
        return filtered

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        """Execute a tool call with strict security, syntax validation, and timeouts."""
        # 1. Role denial check
        if self.role and not self.role.can_use_tool(effect.name):
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Security Denial: Role '{self.role.name}' is not permitted to execute tool '{effect.name}'",
                is_error=True,
            )

        # 2. Check path denial if path argument is present
        if self.role and "path" in effect.arguments:
            req_path = str(effect.arguments["path"])
            if not self.role.can_access_path(req_path):
                return ToolCompleted(
                    call_id=effect.call_id,
                    tool_name=effect.name,
                    output=f"Security Denial: Role '{self.role.name}' is denied access to path '{req_path}'",
                    is_error=True,
                )

        # 3. Dispatch tool execution
        func = self._custom_tools.get(effect.name)
        if not func:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Error: Unknown tool '{effect.name}'",
                is_error=True,
            )

        try:
            out = func(**effect.arguments)
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=str(out),
                is_error=False,
            )
        except PathTraversalError as e:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Security Error: {str(e)}",
                is_error=True,
            )
        except SyntaxValidationError as e:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Syntax Error: {str(e)}",
                is_error=True,
            )
        except Exception as e:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Execution Error: {str(e)}",
                is_error=True,
            )

    def _register_default_tools(self) -> None:
        """Register secure standard tools."""

        def read_file(path: str, offset: int = 1, limit: int = 100) -> str:
            target = resolve_sandbox_path(self.workspace_root, path)
            if not target.exists():
                return f"Error: File '{path}' does not exist."
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(0, offset - 1)
            end = start + limit
            sliced = lines[start:end]
            numbered = [f"{start + i + 1}: {line}" for i, line in enumerate(sliced)]
            return "\n".join(numbered)

        self.register(
            name="read_file",
            description="Read lines from a file in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "offset": {"type": "integer", "description": "1-based starting line", "default": 1},
                    "limit": {"type": "integer", "description": "Max lines to read", "default": 100},
                },
                "required": ["path"],
            },
            func=read_file,
        )

        def write_file(path: str, content: str) -> str:
            target = resolve_sandbox_path(self.workspace_root, path)

            # Pre-write AST validation for Python files
            if path.endswith(".py"):
                try:
                    ast.parse(content, filename=path)
                except SyntaxError as e:
                    raise SyntaxValidationError(f"Invalid Python syntax on line {e.lineno}: {e.msg}")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"

        self.register(
            name="write_file",
            description="Write full text content to a file in the workspace (Python files are syntax-checked before saving).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["path", "content"],
            },
            func=write_file,
        )

        def run_command(command: str, timeout: Optional[int] = None) -> str:
            t = timeout or self.timeout
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=t,
            )
            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"
            return out or "(no output)"

        self.register(
            name="run_command",
            description="Run a shell command inside the workspace directory.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
            func=run_command,
        )


# -----------------------------------------------------------------------------
# MCP (Model Context Protocol) Tool Adapter
# -----------------------------------------------------------------------------

class McpToolAdapter(ToolRunner):
    """Adapts external Model Context Protocol (MCP) tool servers into arity ToolRunner."""

    def __init__(self, mcp_client_callable: Optional[Callable[[str, dict], str]] = None):
        self._mcp_callable = mcp_client_callable or self._default_mcp_stub
        self._mcp_tools: dict[str, dict[str, Any]] = {}

    def register_mcp_tool(self, tool_def: dict[str, Any]) -> None:
        """Register a tool definition received from an MCP tools/list response."""
        name = tool_def.get("name", "")
        self._mcp_tools[name] = tool_def

    def get_schemas(self) -> list[dict[str, Any]]:
        """Convert MCP inputSchema into OpenAI function format."""
        schemas = []
        for name, tool_def in self._mcp_tools.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool_def.get("description", ""),
                    "parameters": tool_def.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })
        return schemas

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        if effect.name not in self._mcp_tools:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Error: Unknown MCP tool '{effect.name}'",
                is_error=True,
            )

        try:
            res = self._mcp_callable(effect.name, effect.arguments)
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=str(res),
                is_error=False,
            )
        except Exception as e:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"MCP Error: {str(e)}",
                is_error=True,
            )

    def _default_mcp_stub(self, name: str, args: dict[str, Any]) -> str:
        return f"MCP Tool '{name}' executed with args: {json.dumps(args)}"
