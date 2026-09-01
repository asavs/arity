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


class QuotaWallError(ProviderError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    call: Callable[..., Any]

    def wire(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class TurnResult:
    text: str
    tokens: int
    rounds: int
    tool_calls_executed: int = 0


class ChatHarness:
    def __init__(self, ledger: Ledger, timeout: int = 90):
        self.ledger = ledger
        self.timeout = timeout
        self.calls = 0

    def turn(self, seat: Seat, messages: list[dict[str, Any]], prompt: str,
             tools: dict[str, Tool] | None, tool_log: list[dict[str, Any]],
             max_tokens: int = 700, effort: str = "medium") -> TurnResult:
        if prompt:
            messages.append({"role": "user", "content": prompt})

        tool_calls_count = 0
        for round_idx in range(8):
            body: dict[str, Any] = {"model": seat.model, "messages": messages, "max_tokens": max_tokens}
            if tools:
                body["tools"] = [tool.wire() for tool in tools.values()]

            data = self._post(seat, body)
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            messages.append(msg)

            raw_calls = msg.get("tool_calls") or []
            if not raw_calls:
                usage = data.get("usage", {})
                tokens = usage.get("total_tokens", len(prompt.split()) + 30)
                return TurnResult(msg.get("content") or "", tokens, round_idx + 1, tool_calls_count)

            for raw in raw_calls:
                tool_calls_count += 1
                fn = raw.get("function", {})
                fn_name = fn.get("name", "")
                call_id = raw.get("id", f"call_{tool_calls_count}")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {}

                if tools and fn_name in tools:
                    try:
                        res = tools[fn_name].call(**args)
                        tool_log.append({"id": call_id, "name": fn_name, "arguments": args, "status": "ok", "result": res})
                        messages.append({"role": "tool", "tool_call_id": call_id, "name": fn_name, "content": json.dumps(res, default=str)})
                    except Exception as exc:
                        tool_log.append({"id": call_id, "name": fn_name, "arguments": args, "status": "error", "error": str(exc)})
                        messages.append({"role": "tool", "tool_call_id": call_id, "name": fn_name, "content": json.dumps({"error": str(exc)})})
                else:
                    err_msg = f"tool {fn_name!r} not available"
                    tool_log.append({"id": call_id, "name": fn_name, "arguments": args, "status": "error", "error": err_msg})
                    messages.append({"role": "tool", "tool_call_id": call_id, "name": fn_name, "content": json.dumps({"error": err_msg})})

        raise RuntimeError("tool loop exceeded eight real model rounds")

    def _post(self, seat: Seat, body: dict[str, Any]) -> dict[str, Any]:
        key = self.ledger.credential_for_proxy(seat.id)
        if seat.remaining <= 0:
            raise QuotaWallError(429, "seat quota exhausted")

        req = urllib.request.Request(
            seat.endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "User-Agent": "arity-v0/python-urllib"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.calls += 1
                self.ledger.meter(seat, data.get("usage", {}).get("total_tokens", 50))
                return data
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code in (429, 402) or "quota" in err_body.casefold():
                raise QuotaWallError(exc.code, err_body) from exc
            raise ProviderError(exc.code, err_body) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(0, str(exc.reason)) from exc
