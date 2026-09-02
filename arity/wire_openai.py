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
            "messages": [{"role": "system", "content": effect.system}] + effect.messages,
        }
        if effect.tools:
            body["tools"] = [_tool(t) for t in effect.tools]

        request = urllib.request.Request(
            self.seat.url,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {os.environ[self.seat.key_env]}",
            },
        )
        with urllib.request.urlopen(request) as response:
            reply = json.load(response)

        seats.spend(self.seat.id, reply["usage"]["completion_tokens"] / 1_000_000)
        return _completed(reply)


def _tool(schema: dict) -> dict:
    """OpenAI wraps the same three fields in {"type": "function", "function": {...}}."""
    return {"type": "function", "function": {
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["input_schema"],
    }}


def _completed(reply: dict) -> ModelCompleted:
    message = reply["choices"][0]["message"]
    calls = [{"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]}
             for c in message.get("tool_calls") or []]
    return ModelCompleted(text=message.get("content") or "", tool_calls=calls, usage=reply["usage"])
