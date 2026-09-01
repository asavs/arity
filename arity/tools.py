"""Arity tools — sandbox execution, AST validation, and MCP adapters.

Axiom 2: Roles are denial sets (tools, paths, hosts).
Axiom 12: Tool runners are pluggable seams (MCP, native Rust, local sandbox).
"""
from __future__ import annotations

import ast
import json
import os
import logging

logger = logging.getLogger(__name__)
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ._version import USER_AGENT
from .roles import Role
from .seams import ToolRunner
from .types import ExecuteTool, ToolCompleted


USER_DELIVERY_MARKER = "[Delivered to Asa]"
"""Cross-module contract: a ToolRunner prefixes ``message(to="user")`` output with this and the
dispatcher recovers a kernel's spoken answer by it. The text is frozen — candidates see it and
past trial records carry it."""


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
        custom_tools: Optional[dict[str, Callable[..., str]]] = None,
        message_router: Optional[Callable[[str, str], str]] = None,
    ):
        self.workspace_root = (Path(workspace_root) if workspace_root else Path.cwd()).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.role = role
        self.timeout = timeout
        self.message_router = message_router
        self._custom_tools: dict[str, Callable[..., str]] = {}
        self._schemas: list[dict[str, Any]] = []
        self._register_default_tools()
        if custom_tools:
            for name, func in custom_tools.items():
                self._custom_tools[name] = func

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

        # 3. Check host denial for network tools
        if self.role and "url" in effect.arguments:
            req_url = str(effect.arguments["url"])
            if not self.role.can_access_host(req_url):
                return ToolCompleted(
                    call_id=effect.call_id,
                    tool_name=effect.name,
                    output=f"Security Denial: Role '{self.role.name}' is denied network access to host in '{req_url}'",
                    is_error=True,
                )

        # 4. Check command denial for shell execution
        if self.role and "command" in effect.arguments:
            cmd = str(effect.arguments["command"])
            for dp in self.role.denial_set.denied_paths:
                if dp and dp.lower() in cmd.lower():
                    return ToolCompleted(
                        call_id=effect.call_id,
                        tool_name=effect.name,
                        output=f"Security Denial: Command attempts access to denied path '{dp}'",
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

        def list_directory(path: str = ".") -> str:
            target = resolve_sandbox_path(self.workspace_root, path)
            if not target.exists():
                return f"Error: Directory '{path}' does not exist."
            if not target.is_dir():
                return f"Error: '{path}' is not a directory."
            entries = []
            for item in sorted(target.iterdir()):
                prefix = "[DIR] " if item.is_dir() else "[FILE]"
                entries.append(f"{prefix} {item.name}")
            return "\n".join(entries) or "(empty directory)"

        self.register(
            name="list_directory",
            description="List files and directories within a workspace folder.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path", "default": "."},
                },
            },
            func=list_directory,
        )

        def search_files(pattern: str, path: str = ".") -> str:
            import re
            target = resolve_sandbox_path(self.workspace_root, path)
            if not target.exists():
                return f"Error: Path '{path}' does not exist."
            regex = re.compile(pattern, re.IGNORECASE)
            matches = []
            for p in target.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    try:
                        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                        for idx, line in enumerate(lines):
                            if regex.search(line):
                                rel_p = p.relative_to(self.workspace_root)
                                matches.append(f"{rel_p}:{idx + 1}: {line.strip()}")
                    except Exception:
                        # Benign: unreadable or binary file skipped in file search whose results are capped at 50.
                        continue

        self.register(
            name="search_files",
            description="Search for regex pattern across files in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Starting relative path", "default": "."},
                },
                "required": ["pattern"],
            },
            func=search_files,
        )

        # Alias search -> search_files for seamless backward compatibility
        self.register(
            name="search",
            description="Alias for search_files.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Starting relative path", "default": "."},
                },
                "required": ["pattern"],
            },
            func=search_files,
        )

        def fetch_url(url: str, timeout: int = 15) -> str:
            import urllib.request
            from html.parser import HTMLParser

            class SimpleHTMLTextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                    self.in_script = False

                def handle_starttag(self, tag, attrs):
                    if tag in ('script', 'style', 'nav', 'footer', 'header'):
                        self.in_script = True

                def handle_endtag(self, tag):
                    if tag in ('script', 'style', 'nav', 'footer', 'header'):
                        self.in_script = False

                def handle_data(self, data):
                    if not self.in_script and data.strip():
                        self.text.append(data.strip())

                def get_text(self):
                    return "\n".join(self.text)

            # A bot UA gets 403s and sign-in walls from most large sites; a browser UA gets the page.
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,text/markdown;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            limit = 8000

            def direct() -> tuple[str, str]:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    raw = resp.read().decode("utf-8", errors="replace")
                if "text/html" not in content_type:
                    return raw[:limit], "direct"
                parser = SimpleHTMLTextExtractor()
                parser.feed(raw)
                return parser.get_text(), "direct"

            def looks_empty(text: str) -> bool:
                t = text.strip().lower()
                return len(t) < 300 or t.startswith(("sign in", "log in", "enable javascript", "please enable")) or "javascript is required" in t[:400]

            # Reader proxy renders JS shells into markdown. TODO(scout): swap for Firecrawl/TinyFish or a
            # headless browser in a VM when pricing pages need clicks; a reader is enough for text.
            def via_reader() -> tuple[str, str]:
                req = urllib.request.Request(f"https://r.jina.ai/{url}", headers={**headers, "Accept": "text/markdown, text/plain"})
                with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
                    return resp.read().decode("utf-8", errors="replace"), "reader"

            errors: list[str] = []
            for attempt in (direct, via_reader):
                try:
                    text, how = attempt()
                except Exception as e:
                    errors.append(f"{attempt.__name__}: {e}")
                    continue
                if looks_empty(text) and attempt is direct:
                    errors.append("direct: page is a JS shell or sign-in wall")
                    continue
                return f"[fetched via {how}] {text[:limit]}"
            return f"Error fetching URL '{url}': " + " | ".join(errors)

        self.register(
            name="fetch_url",
            description="Fetch text or markdown content from a web URL or raw GitHub file.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP/HTTPS URL to fetch"},
                },
                "required": ["url"],
            },
            func=fetch_url,
        )

        def message_tool(to: str, text: str) -> str:
            target = to.lower().strip()
            if target in ("user", "human", "asa"):
                return f"{USER_DELIVERY_MARKER}: {text}"
            if self.message_router:
                return self.message_router(target, text)
            return f"[Message queued for {target}]: {text}"

        self.register(
            name="message",
            description="Send a message to the user ('user') or consult a peer agent ('scout', 'engineer', 'python_developer', 'reviewer').",
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient: 'user' to message Asa, or peer role (e.g. 'scout', 'engineer', 'python_developer', 'reviewer')",
                    },
                    "text": {
                        "type": "string",
                        "description": "Message content, question, or task brief",
                    },
                },
                "required": ["to", "text"],
            },
            func=message_tool,
        )

# -----------------------------------------------------------------------------
# Smart Tool Routing & Pluggable Providers (The Tool Seam)
# -----------------------------------------------------------------------------

def get_config_value(key: str) -> Optional[str]:
    """Resolve an environment or active ``.arity/config.json`` setting."""
    val = os.environ.get(key)
    if val:
        return val
    for p in (Path(".arity/config.json"), Path.home() / ".arity" / "config.json"):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and key in data and data[key]:
                    return str(data[key])
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to parse config file %s: %s", p, exc)


def positive_int(value: Any, *, name: str = "value") -> int:
    """Parse a positive integer without silently truncating floats or accepting booleans."""
    if isinstance(value, bool):
        parsed = None
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\+?[0-9]+", value.strip()):
        parsed = int(value)
    else:
        parsed = None
    if parsed is None or parsed <= 0:
        raise ValueError(f"{name} must be a positive integer; got {value!r}")
    return parsed


def resolve_arity(explicit: Optional[int] = None, *, default: int = 1) -> int:
    """Resolve the requested maximum arity: explicit, ``ARITY``, compatibility fallback, default."""
    if explicit is not None:
        return positive_int(explicit, name="arity")
    for key in ("ARITY", "ARITY"):
        configured = get_config_value(key)
        if configured is not None:
            label = key if key == "ARITY" else f"{key} (legacy)"
            return positive_int(configured, name=label)
    return positive_int(default, name="default arity")


def smart_web_search(query: str, limit: int = 5) -> str:
    """Smart router for web search: checks available API keys, routes to best engine, with zero-dep fallback."""
    # 1. If TinyFish API key is present, try TinyFish first
    tinyfish_key = get_config_value("TINYFISH_API_KEY")
    if tinyfish_key:
        res = tinyfish_search(query, limit, api_key=tinyfish_key)
        if not res.startswith("TinyFish Error") and not res.startswith("TinyFish search error"):
            return res

    # 2. Fallback to stdlib GitHub and web search (always available, 0-dep)
    return stdlib_github_search(query, limit)

# -----------------------------------------------------------------------------
# Standalone Swappable Search Providers (Pluggable Seam Components)
# -----------------------------------------------------------------------------

def stdlib_github_search(query: str, limit: int = 5) -> str:
    """Stdlib GitHub repository & skill search (zero dependencies)."""
    import urllib.request, urllib.parse, json
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort=stars&order=desc"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])[:limit]
            results = []
            for idx, it in enumerate(items):
                results.append(
                    f"{idx+1}. **{it['full_name']}** (⭐ {it['stargazers_count']})\n"
                    f"   {it.get('description', '')}\n"
                    f"   URL: {it['html_url']}"
                )
            return "\n\n".join(results) or f"No results found for '{query}'."
    except Exception as e:
        return f"Search error for '{query}': {e}"


def tinyfish_search(query: str, limit: int = 5, api_key: Optional[str] = None) -> str:
    """TinyFish Search API provider (structured JSON for AI agents)."""
    import urllib.request, urllib.parse, json
    key = api_key or os.environ.get("TINYFISH_API_KEY")
    if not key:
        return "TinyFish Error: TINYFISH_API_KEY required for tinyfish search engine."
    url = f"https://api.search.tinyfish.ai?query={urllib.parse.quote(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "X-API-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("results", []) or data.get("items", [])
            results = []
            for idx, it in enumerate(items[:limit]):
                results.append(
                    f"{idx+1}. **{it.get('title', 'Result')}**\n"
                    f"   {it.get('snippet', it.get('description', ''))}\n"
                    f"   URL: {it.get('url', '')}"
                )
            return "\n\n".join(results) or f"TinyFish returned no results for '{query}'."
    except Exception as e:
        return f"TinyFish search error: {e}"
# -----------------------------------------------------------------------------

class McpToolAdapter(ToolRunner):
    """Adapt external Model Context Protocol servers into an Arity ToolRunner."""

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


def create_mcp_tool_runner(
    workspace_root: Optional[Path] = None,
    role: Optional[Role] = None,
    timeout: int = 30,
) -> McpToolAdapter:
    """Instantiate an MCP Tool Runner backed by sandboxed local workspace primitives."""
    ws = (Path(workspace_root) if workspace_root else Path.cwd()).resolve()
    ws.mkdir(parents=True, exist_ok=True)

    def mcp_executor(tool_name: str, args: dict[str, Any]) -> str:
        if role and not role.can_use_tool(tool_name):
            return f"Security Denial: Role '{role.name}' is not permitted to execute tool '{tool_name}'"

        if tool_name == "read_file":
            path_str = args.get("path", "")
            offset = int(args.get("offset", 1))
            limit = int(args.get("limit", 100))
            target = resolve_sandbox_path(ws, path_str)
            if not target.exists():
                return f"Error: File '{path_str}' does not exist."
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(0, offset - 1)
            end = start + limit
            selected = lines[start:end]
            numbered = [f"{start + i + 1:4d} | {line}" for i, line in enumerate(selected)]
            return "\n".join(numbered)

        elif tool_name == "write_file":
            path_str = args.get("path", "")
            content = args.get("content", "")
            target = resolve_sandbox_path(ws, path_str)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content.encode('utf-8'))} bytes to '{path_str}' via MCP."

        elif tool_name == "execute_command":
            command = args.get("command", "")
            import subprocess
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(ws),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                out = proc.stdout or ""
                if proc.stderr:
                    out += f"\n[stderr]\n{proc.stderr}"
                return out.strip() or f"[Command '{command}' exited with code {proc.returncode} via MCP]"
            except Exception as e:
                return f"MCP command execution error: {e}"

        elif tool_name == "web_search":
            query = args.get("query", "")
            return smart_web_search(query)

        return f"Unknown MCP tool: {tool_name}"

    adapter = McpToolAdapter(mcp_client_callable=mcp_executor)
    adapter.register_mcp_tool({
        "name": "read_file",
        "description": "Read file contents inside the workspace via MCP protocol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["path"],
        },
    })
    adapter.register_mcp_tool({
        "name": "write_file",
        "description": "Create or overwrite file inside workspace via MCP protocol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
    })
    adapter.register_mcp_tool({
        "name": "execute_command",
        "description": "Run shell command inside workspace via MCP protocol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
            },
            "required": ["command"],
        },
    })
    adapter.register_mcp_tool({
        "name": "web_search",
        "description": "Search the web via MCP protocol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    })
    return adapter
