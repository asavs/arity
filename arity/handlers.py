"""Standard-library default handlers for Arity seams.

Zero third-party dependencies. Built purely on Python 3.13 stdlib.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import logging
from .diagnostics import record_data_loss

logger = logging.getLogger(__name__)
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ._version import USER_AGENT

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[<>=]|\x1b\][^\x07]*\x07")
"""CSI sequences, private-mode toggles, and OSC strings emitted by terminal-UI CLIs."""

from .seams import ModelProvider, Observer, RecordStore, ToolRunner, Transport
from .types import (
    CallModel,
    Effect,
    EmitMessage,
    Event,
    ExecuteTool,
    ModelCompleted,
    ModelFailed,
    State,
    StoreRecord,
    ToolCompleted,
)


# -----------------------------------------------------------------------------
# 1. Model Provider (OpenAI-compatible /chat/completions over stdlib urllib)
# -----------------------------------------------------------------------------

@dataclass
class OpenAIModelProvider:
    """Calls any OpenAI-compatible /chat/completions endpoint using standard library urllib."""
    api_key: Optional[str] = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    timeout: float = 60.0

    def __post_init__(self):
        if not self.api_key:
            self.api_key = (
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY")
                or ""
            )

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": effect.messages,
            "temperature": effect.temperature,
        }
        if effect.tools:
            payload["tools"] = effect.tools
        if effect.max_tokens:
            payload["max_tokens"] = effect.max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_body = resp.read().decode("utf-8")
                res = json.loads(raw_body)
                choice = res.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content")
                tool_calls = message.get("tool_calls", [])
                usage = res.get("usage", {})
                finish_reason = choice.get("finish_reason", "stop")

                return ModelCompleted(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason=finish_reason,
                    seat_id=f"{self.base_url}:{self.model}",
                )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (429, 500, 502, 503, 504)
            return ModelFailed(
                error=f"HTTP {e.code}: {err_body}",
                seat_id=f"{self.base_url}:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Request failed: {str(e)}",
                seat_id=f"{self.base_url}:{self.model}",
                retryable=True,
            )


@dataclass
class GeminiModelProvider:
    """Direct Google Generative AI provider over stdlib urllib."""
    api_key: Optional[str] = None
    model: str = "gemini-3.6-flash"
    timeout: float = 60.0

    def __post_init__(self):
        if not self.api_key:
            self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        from .gemini_format import to_contents, tool_declarations, parse_parts, usage_from
        contents, system_text = to_contents(effect.messages)
        system_parts = [system_text] if system_text else []

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": effect.temperature,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        if effect.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = effect.max_tokens

        decls = tool_declarations(effect.tools)
        if decls:
            payload["tools"] = [{"functionDeclarations": decls}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_body = resp.read().decode("utf-8")
                res = json.loads(raw_body)
                candidates = res.get("candidates", [{}])
                candidate = candidates[0] if candidates else {}
                parts = candidate.get("content", {}).get("parts", [])

                content, tool_calls = parse_parts(parts)
                usage = usage_from(res.get("usageMetadata", {}))

                return ModelCompleted(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason=candidate.get("finishReason", "STOP").lower(),
                    seat_id=f"gemini:{self.model}",
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (429, 500, 502, 503, 504)
            return ModelFailed(
                error=f"HTTP {e.code}: {err_body}",
                seat_id=f"gemini:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Request failed: {str(e)}",
                seat_id=f"gemini:{self.model}",
                retryable=True,
            )


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Render a chat message list as the single prompt string a CLI harness takes on argv."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system" and content:
            lines.append(f"[System Context]\n{content}\n")
        elif role == "user" and content:
            lines.append(f"{content}")
        elif role == "assistant" and content:
            lines.append(f"[Assistant]\n{content}")

    return "\n".join(lines).strip()


@dataclass
class CLIModelProvider:
    """Harness / ACP Provider that executes tasks through authenticated CLI subscriptions (codex, claude, omp)."""
    harness: str = "codex"  # "codex" | "claude" | "omp"
    model: str = "gpt-5.6-sol"
    timeout: float = 120.0
    # The CLI has its own tools. Without a cwd it acts in the process's directory - the repo -
    # instead of the candidate's sandbox. The dispatcher sets this to the sandbox.
    cwd: Optional[str] = None

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        full_prompt = _flatten_messages(effect.messages)

        if self.harness == "codex":
            cmd = ["codex", "exec", "--skip-git-repo-check", full_prompt]
        elif self.harness == "claude":
            cmd = ["claude", "-p", full_prompt]
        else:
            cmd = [self.harness, full_prompt]

        # Windows installs CLIs as .cmd shims; without a shell they need their resolved path.
        cmd[0] = shutil.which(cmd[0]) or cmd[0]
        try:
            # No shell and no stdin: a CLI that wants to go interactive (login prompt, TUI) fails
            # fast instead of waiting on a terminal that isn't there. Without shell=True the
            # timeout kills the CLI itself, not a shell wrapper whose child keeps the pipes open.
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                stdin=subprocess.DEVNULL,
                cwd=self.cwd,
            )
            raw_output = proc.stdout or ""
            if proc.returncode != 0 and not raw_output.strip():
                return ModelFailed(
                    error=f"CLI harness '{self.harness}' failed (exit code {proc.returncode}): {proc.stderr}",
                    seat_id=f"{self.harness}:{self.model}",
                    retryable=False,
                )

            # Clean output: TUI escape/control sequences (omp, claude) and harness banners.
            output = _ANSI_RE.sub("", raw_output).strip()
            if self.harness == "codex" and "tokens used" in output:
                # Extract content after token summary or raw lines
                output_lines = [l for l in output.splitlines() if not l.startswith("2026-") and "rmcp::transport" not in l]
                output = "\n".join(output_lines).strip()

            return ModelCompleted(
                content=output,
                tool_calls=[],
                usage={"prompt_tokens": len(full_prompt) // 4, "completion_tokens": len(output) // 4},
                finish_reason="stop",
                seat_id=f"{self.harness}:{self.model}",
            )
        except subprocess.TimeoutExpired:
            return ModelFailed(
                error=f"CLI harness '{self.harness}' timed out after {self.timeout}s",
                seat_id=f"{self.harness}:{self.model}",
                retryable=True,
            )
        except Exception as e:
            return ModelFailed(
                error=f"CLI execution failed: {str(e)}",
                seat_id=f"{self.harness}:{self.model}",
                retryable=True,
            )


def create_model_provider(seat: Any) -> ModelProvider:
    """Factory creating the appropriate ModelProvider for any given Seat (Wire with Harness Fallback)."""
    try:
        from .wire import create_wire_model_provider
        return create_wire_model_provider(seat)
    except (TypeError, AttributeError):
        raise
    except Exception as exc:
        logger.debug("Wire model provider not created for seat %s: %s", getattr(seat, "id", seat), exc)
    provider = getattr(seat, "provider", "").lower()
    model = getattr(seat, "model", "")
    api_key = getattr(seat, "api_key", None)
    endpoint = getattr(seat, "endpoint", "")

    if provider == "gemini":
        return GeminiModelProvider(api_key=api_key, model=model or "gemini-3.6-flash")
    elif provider in ("codex", "claude", "omp", "cli"):
        return CLIModelProvider(harness=provider, model=model or "default")
    else:
        return OpenAIModelProvider(
            api_key=api_key,
            base_url=endpoint or "https://api.openai.com/v1",
            model=model or "gpt-4o",
        )


def create_default_model_provider() -> ModelProvider:
    """Return the best available live ModelProvider based on wire, OAuth, API keys, and CLIs."""
    try:
        from .wire import create_wire_model_provider, load_local_oauth_credentials
        creds = load_local_oauth_credentials()
        if "openai-codex" in creds:
            class DummyCodexSeat:
                provider = "codex-wire"
                model = "gpt-5.6-sol"
                api_key = None
                endpoint = ""
            return create_wire_model_provider(DummyCodexSeat())
        elif "xai-oauth" in creds:
            class DummyGrokSeat:
                provider = "grok-wire"
                model = "grok-4.5"
                api_key = None
                endpoint = ""
            return create_wire_model_provider(DummyGrokSeat())
    except (TypeError, AttributeError):
        raise
    except Exception as exc:
        logger.debug("Default wire model provider initialization fallback: %s", exc)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return GeminiModelProvider()
    elif shutil.which("codex"):
        return CLIModelProvider(harness="codex")
    elif shutil.which("claude"):
        return CLIModelProvider(harness="claude")
    return OpenAIModelProvider()

# -----------------------------------------------------------------------------
# 2. Local Tool Runner (Stdlib filesystem and command execution)
# -----------------------------------------------------------------------------

class LocalToolRunner:
    """Registry and executor for local python functions and tools."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self._tools: dict[str, Callable[..., str]] = {}
        self._schemas: list[dict[str, Any]] = []
        self._register_defaults()

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., str],
    ):
        """Register a custom tool function with JSON schema."""
        self._tools[name] = func
        self._schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })

    def get_schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    def execute(self, effect: ExecuteTool) -> ToolCompleted:
        func = self._tools.get(effect.name)
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
        except Exception as e:
            return ToolCompleted(
                call_id=effect.call_id,
                tool_name=effect.name,
                output=f"Execution error: {str(e)}",
                is_error=True,
            )

    def _register_defaults(self):
        """Register standard baseline tools: read_file, write_file, run_command."""

        def read_file(path: str, offset: int = 1, limit: int = 100) -> str:
            target = (self.workspace_root / path).resolve()
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
                    "path": {"type": "string", "description": "Relative path to file"},
                    "offset": {"type": "integer", "description": "1-based starting line", "default": 1},
                    "limit": {"type": "integer", "description": "Max lines to read", "default": 100},
                },
                "required": ["path"],
            },
            func=read_file,
        )

        def write_file(path: str, content: str) -> str:
            target = (self.workspace_root / path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"

        self.register(
            name="write_file",
            description="Write full text content to a file in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to file"},
                    "content": {"type": "string", "description": "Text content to write"},
                },
                "required": ["path", "content"],
            },
            func=write_file,
        )

        def run_command(command: str, timeout: int = 30) -> str:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout,
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
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
            func=run_command,
        )


# -----------------------------------------------------------------------------
# 3. JSONL Record Store (Append-only persistence)
# -----------------------------------------------------------------------------

def default_record_store() -> "RecordStore":
    """Select Arity's store while retaining legacy settings and state paths."""
    from .record_readers import configured_store_spec
    spec = configured_store_spec()
    if spec.backend == "sqlite":
        from .stores.sqlite import SqliteRecordStore
        return SqliteRecordStore(spec.path)
    return JsonlRecordStore(spec.path)


class JsonlRecordStore:
    """Simple append-only JSONL record store."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else Path(".arity/records")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # parallel candidates append concurrently; interleaved lines corrupt JSONL

    def _path(self, kind: str) -> Path:
        return self.root / f"{kind}.jsonl"

    def append(self, effect: StoreRecord) -> None:
        rec = dict(effect.record)
        rec.setdefault("timestamp", time.time())
        p = self._path(effect.kind)
        with self._lock, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def query(self, kind: str, **filters: Any) -> list[dict[str, Any]]:
        p = self._path(kind)
        if not p.exists():
            return []
        results = []
        with self._lock:
            lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if all(row.get(k) == v for k, v in filters.items()):
                    results.append(row)
            except Exception as exc:
                logger.warning("Corrupt line in JSONL record store %s: %s", p, exc)
                record_data_loss(f"JsonlCorruptLine({kind})", exc)
                continue
        return results

# -----------------------------------------------------------------------------
# 4. Console Transport (Terminal formatting)
# -----------------------------------------------------------------------------

class ConsoleTransport:
    """Prints incoming and outgoing messages to stdout with styling."""

    def __init__(self, bot_name: str = "arity"):
        self.bot_name = bot_name

    def emit(self, effect: EmitMessage) -> None:
        if effect.text:
            print(f"\n\033[1;36m[{self.bot_name}]\033[0m {effect.text}\n")


# -----------------------------------------------------------------------------
# 5. Metrics & Audit Observer
# -----------------------------------------------------------------------------

@dataclass
class MetricsObserver:
    """Tracks token counts, tool calls, cache hits, and event flow for telemetry and evals."""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    total_tool_calls: int = 0
    failed_tool_calls: int = 0
    events_seen: int = 0
    effects_seen: int = 0
    fallback_events: int = 0

    @property
    def cache_hit_ratio(self) -> float:
        """Fraction of prompt tokens served from cache (Axiom 7)."""
        return self.cached_prompt_tokens / max(1, self.total_prompt_tokens)

    @property
    def tool_success_ratio(self) -> float:
        """Fraction of tool calls executed without runtime errors."""
        if self.total_tool_calls == 0:
            return 1.0
        return max(0.0, (self.total_tool_calls - self.failed_tool_calls) / self.total_tool_calls)

    def on_event(self, state: State, event: Event) -> None:
        self.events_seen += 1
        if isinstance(event, ModelCompleted) and event.usage:
            self.total_prompt_tokens += event.usage.get("prompt_tokens", 0)
            self.total_completion_tokens += event.usage.get("completion_tokens", 0)
            self.cached_prompt_tokens += event.usage.get("cached_tokens", 0) or event.usage.get("cache_read_input_tokens", 0)
        if isinstance(event, ToolCompleted):
            self.total_tool_calls += 1
            if event.is_error:
                self.failed_tool_calls += 1

    def on_effect(self, state: State, effect: Effect) -> None:
        self.effects_seen += 1
