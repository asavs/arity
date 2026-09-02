"""Wire: the plug behind the Model seam, for anything OpenAI-shaped.

OpenAI, xAI, Gemini's compatibility endpoint, a local server: all take the
same request. The seat's URL says where. Same contract as wire_anthropic.py:
payload and seat in, ModelCompleted out.
"""
from __future__ import annotations

import json
import os
import urllib.request

from . import seats
from .types import CallModel, ModelCompleted


class OpenAIWire:
    def __init__(self, seat_id: str, model: str):
        self.seat = seats.lookup(seat_id)
        self.model = model

    def call(self, effect: CallModel) -> ModelCompleted:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": effect.system}] + _format_messages(effect.messages),
        }
        if effect.tools:
            body["tools"] = [_tool(t) for t in effect.tools]

        request = urllib.request.Request(
            self.seat.url,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {os.environ[self.seat.key_env]}",
                "user-agent": "Arity/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {e.code} from {self.seat.url} for model {self.model}: {error_body}") from e
        usage = reply.get("usage") or {}
        tokens = usage.get("completion_tokens") or 0
        seats.spend(self.seat.id, tokens / 1_000_000)
        return _completed(reply)

def _format_message(m: dict) -> dict:
    msg = dict(m)
    if "tool_calls" in msg and msg["tool_calls"]:
        formatted = []
        for c in msg["tool_calls"]:
            if "function" in c:
                formatted.append(c)
            else:
                args = c.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args)
                formatted.append({
                    "id": c.get("id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": c.get("name", ""),
                        "arguments": args,
                    },
                })
        msg["tool_calls"] = formatted
    return msg


def _format_messages(messages: list[dict]) -> list[dict]:
    formatted = []
    i = 0
    while i < len(messages):
        msg = _format_message(messages[i])
        formatted.append(msg)

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected_ids = {c["id"]: c.get("function", {}).get("name", "tool")
                            for c in msg["tool_calls"] if "id" in c}
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msg = _format_message(messages[j])
                formatted.append(tool_msg)
                expected_ids.pop(tool_msg.get("tool_call_id"), None)
                j += 1
            for missing_id, tool_name in expected_ids.items():
                formatted.append({
                    "role": "tool",
                    "tool_call_id": missing_id,
                    "name": tool_name,
                    "content": "(no tool output / interrupted)",
                })
            i = j
        else:
            i += 1

    return formatted


def _tool(schema: dict) -> dict:
    """OpenAI wraps the same three fields in {"type": "function", "function": {...}}."""
    return {"type": "function", "function": {
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["input_schema"],
    }}


def _completed(reply: dict) -> ModelCompleted:
    message = reply["choices"][0]["message"]
    calls = [{"id": c.get("id") or "call_0", "name": c["function"]["name"], "arguments": c["function"]["arguments"]}
             for c in message.get("tool_calls") or []]
    return ModelCompleted(text=message.get("content") or "", tool_calls=calls, usage=reply.get("usage") or {})
