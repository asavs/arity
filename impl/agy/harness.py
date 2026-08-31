"""harness.py - OpenAI-compatible HTTP chat completions runner with tool execution loop."""

from __future__ import annotations
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


class ApiError(RuntimeError):
    """General API communication error."""
    pass


class QuotaWallError(ApiError):
    """Raised when hitting an API quota wall / rate limit."""
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class TurnResult:
    content: str
    tool_calls_executed: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


class Harness:
    def __init__(self, timeout_sec: float = 60.0) -> None:
        self.timeout_sec = timeout_sec

    def _post(
        self,
        endpoint: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = endpoint.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "arity/0.1",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                if status >= 400:
                    raise ApiError(f"HTTP {status}: {body}")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code in (429, 402):
                raise QuotaWallError(f"HTTP {e.code} Quota wall hit: {err_body}") from e
            raise ApiError(f"HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise ApiError(f"Connection failed to {url}: {e.reason}") from e

    def run_turn(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        tools_spec: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], str] | None = None,
        max_tool_iterations: int = 8,
        temperature: float = 0.2,
    ) -> TurnResult:
        cur_messages = list(messages)
        executed_tool_calls: list[ToolCall] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        final_content = ""
        last_resp: dict[str, Any] = {}

        for _ in range(max_tool_iterations):
            payload: dict[str, Any] = {
                "model": model,
                "messages": cur_messages,
                "temperature": temperature,
            }
            if tools_spec:
                payload["tools"] = tools_spec
                payload["tool_choice"] = "auto"

            last_resp = self._post(endpoint, api_key, payload)
            usage = last_resp.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            choices = last_resp.get("choices", [])
            if not choices:
                break

            msg = choices[0].get("message", {})
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls", [])

            cur_messages.append(msg)
            if not tool_calls:
                final_content = content
                break

            for tc in tool_calls:
                tc_id = tc.get("id", "call_default")
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {}

                t_call = ToolCall(id=tc_id, name=fn_name, arguments=args)
                executed_tool_calls.append(t_call)

                result_str = ""
                if tool_executor:
                    try:
                        result_str = tool_executor(fn_name, args)
                    except Exception as err:
                        result_str = f"Error executing tool {fn_name}: {err}"
                else:
                    result_str = f"Tool {fn_name} executed."

                cur_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_str,
                })
        else:
            final_content = cur_messages[-1].get("content", "")

        return TurnResult(
            content=final_content,
            tool_calls_executed=executed_tool_calls,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            raw_response=last_resp,
        )
