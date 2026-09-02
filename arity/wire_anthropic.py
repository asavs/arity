"""Wire: the plug behind the Model seam, for Anthropic.

Takes the payload and a seat id. Asks the seat table for the URL and key.
Writes the provider's JSON. Sends it. Reads the reply back into a
ModelCompleted event. That is all a wire does.

This is the only code in the package that knows what Anthropic's request
looks like. If cache breakpoints ever matter, this is the only file that
would write them. Naive version: one urllib call, no retries, no streaming.
"""
from __future__ import annotations

import json
import os
import urllib.request

from . import seats
from .types import CallModel, ModelCompleted


class AnthropicWire:
    def __init__(self, seat_id: str, model: str):
        self.seat = seats.lookup(seat_id)
        self.model = model

    def call(self, effect: CallModel) -> ModelCompleted:
        body = {
            "model": self.model,
            "max_tokens": 16000,
            "system": effect.system,
            "tools": [_tool(t) for t in effect.tools],
            "messages": [_message(m) for m in effect.messages],
        }
        request = urllib.request.Request(
            self.seat.url,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "x-api-key": os.environ[self.seat.key_env],
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(request) as response:
            reply = json.load(response)

        seats.spend(self.seat.id, reply["usage"]["output_tokens"] / 1_000_000)
        return _completed(reply)


# --- the two directions of translation ---------------------------------------

def _tool(schema: dict) -> dict:
    """Our tool schema is already the Anthropic shape: name, description, input_schema."""
    return schema


def _message(m: dict) -> dict:
    """Our messages are OpenAI-shaped. Anthropic wants tool results as user content blocks."""
    if m["role"] == "tool":
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]}
    if m["role"] == "assistant" and m.get("tool_calls"):
        blocks = [{"type": "text", "text": m["content"]}] if m["content"] else []
        blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                   for c in m["tool_calls"]]
        return {"role": "assistant", "content": blocks}
    return {"role": m["role"], "content": m["content"]}


def _completed(reply: dict) -> ModelCompleted:
    text = "".join(b["text"] for b in reply["content"] if b["type"] == "text")
    calls = [{"id": b["id"], "name": b["name"], "arguments": b["input"]}
             for b in reply["content"] if b["type"] == "tool_use"]
    return ModelCompleted(text=text, tool_calls=calls, usage=reply["usage"])
