"""A deliberately boring OpenAI-compatible loop around real HTTP calls."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from ledger import Ledger, Seat


class ProviderError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"provider HTTP {status}: {message[:500]}")
        self.status = status


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    call: Callable[..., Any]

    def wire(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.parameters}}


@dataclass
class TurnResult:
    text: str
    tokens: int
    rounds: int


class ChatHarness:
    def __init__(self, ledger: Ledger, timeout: int = 90):
        self.ledger, self.timeout, self.calls = ledger, timeout, 0

    def turn(self, seat: Seat, messages: list[dict[str, Any]], prompt: str,
             tools: dict[str, Tool] | None, tool_log: list[dict[str, Any]],
             max_tokens: int = 700) -> TurnResult:
        messages.append({"role": "user", "content": prompt})
        total, rounds = 0, 0
        for _ in range(8):
            body: dict[str, Any] = {"model": seat.model, "messages": messages,
                                    "temperature": 0, "max_tokens": max_tokens}
            if tools:
                body["tools"] = [tool.wire() for tool in tools.values()]
                body["tool_choice"] = "auto"
            answer = self._post(seat, body)
            rounds += 1
            usage = answer.get("usage") or {}
            used = int(usage.get("total_tokens") or
                       (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)))
            total += used
            message = answer["choices"][0]["message"]
            kept = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
            messages.append(kept)
            calls = message.get("tool_calls") or []
            if not calls:
                self.ledger.meter(seat, total)
                return TurnResult(message.get("content") or "", total, rounds)
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                entry = {"name": name, "arguments": fn.get("arguments", "{}"), "ok": False}
                try:
                    if name not in (tools or {}):
                        raise KeyError(f"tool is not available: {name}")
                    args = json.loads(fn.get("arguments") or "{}")
                    result = tools[name].call(**args)  # type: ignore[index]
                    entry.update(ok=True, result=result)
                    content = json.dumps(result, default=str)
                except Exception as exc:  # the model sees an ordinary tool failure and can recover
                    entry["error"] = str(exc)
                    content = json.dumps({"error": str(exc)})
                tool_log.append(entry)
                messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                                 "name": name, "content": content})
        raise RuntimeError("tool loop exceeded eight real model rounds")

    def _post(self, seat: Seat, body: dict[str, Any]) -> dict[str, Any]:
        key = self.ledger.credential_for_proxy(seat.id)
        request = urllib.request.Request(seat.endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": "gorkbot-v0"})
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ProviderError(exc.code, exc.read().decode("utf-8", "replace")) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(0, str(exc.reason)) from exc
