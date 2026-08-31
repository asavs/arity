"""Arity Gemini formatting — one conversion from OpenAI messages to ``contents``.

Used by both Gemini wires (the API-key endpoint and the Antigravity backend) so they cannot drift:
tool calls become functionCall parts, tool results become functionResponse parts, Gemini 3's
thought signatures and Claude-behind-Antigravity's tool ids round-trip, and an empty model turn
is never sent (Gemini rejects `{"text": ""}` and "requests ending with a model turn").
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional


def to_contents(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return (contents, system_text). Trailing empty model turns are dropped."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
        elif role == "assistant":
            parts: list[dict[str, Any]] = []
            if content:
                parts.append({"text": str(content)})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {"raw": fn.get("arguments")}
                call_names[tc.get("id", "")] = fn.get("name", "")
                part: dict[str, Any] = {"functionCall": {"id": tc.get("id", ""), "name": fn.get("name", ""), "args": args}}
                if tc.get("thought_signature"):
                    part["thoughtSignature"] = tc["thought_signature"]
                parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            call_id = msg.get("tool_call_id", "")
            name = msg.get("name") or call_names.get(call_id, "tool")
            contents.append({"role": "user", "parts": [{"functionResponse": {"id": call_id, "name": name, "response": {"output": str(content or "")}}}]})
        else:  # user
            contents.append({"role": "user", "parts": [{"text": str(content or "")}]})
    while contents and contents[-1]["role"] == "model":
        contents.pop()  # a request may not end with a model turn
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "Continue."}]})
    return contents, ("\n\n".join(system_parts) if system_parts else None)


def tool_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decls = []
    for t in tools or []:
        fn = t.get("function", {})
        if fn.get("name"):
            decls.append({"name": fn["name"], "description": fn.get("description", ""),
                          "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return decls


def parse_parts(parts: list[dict[str, Any]]) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Return (text, tool_calls) from a candidate's parts; ids and thought signatures are preserved."""
    text: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for p in parts or []:
        if p.get("text"):
            text.append(p["text"])
        if "functionCall" in p:
            fc = p["functionCall"]
            tc: dict[str, Any] = {
                "id": fc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {"name": fc.get("name"), "arguments": json.dumps(fc.get("args", {}))},
            }
            if p.get("thoughtSignature"):
                tc["thought_signature"] = p["thoughtSignature"]
            tool_calls.append(tc)
    return ("".join(text) if text else None), tool_calls


def usage_from(meta: dict[str, Any]) -> dict[str, int]:
    usage = {
        "prompt_tokens": meta.get("promptTokenCount", 0),
        "completion_tokens": meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0),
        "thought_tokens": meta.get("thoughtsTokenCount", 0),
        "total_tokens": meta.get("totalTokenCount", 0),
    }
    if "cachedContentTokenCount" in meta:
        usage["cache_read_input_tokens"] = meta["cachedContentTokenCount"]
    return usage
