"""gorkbot wire — Direct provider wire protocols & transparent harness fallbacks.

Axiom 13: The Seam Test — Own the pure from-scratch wire protocol, keep the
external harness swappable as a seamless fallback and A/B benchmark.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .handlers import CLIModelProvider, GeminiModelProvider, OpenAIModelProvider
from .seams import ModelProvider
from .types import CallModel, ModelCompleted, ModelFailed


# -----------------------------------------------------------------------------
# 1. Local OAuth Credential Discovery
# -----------------------------------------------------------------------------

def load_local_oauth_credentials() -> dict[str, dict[str, Any]]:
    """Discover active OAuth subscription credentials from local storage.

    Checks ~/.gorkbot/auth.json first, then imports from ~/.omp/agent/agent.db.
    """
    creds: dict[str, dict[str, Any]] = {}

    # 1. Check standalone gorkbot auth store
    gorkbot_auth_file = Path.home() / ".gorkbot" / "auth.json"
    if gorkbot_auth_file.exists():
        try:
            creds.update(json.loads(gorkbot_auth_file.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 2. Check local OMP SQLite store
    omp_db = Path.home() / ".omp" / "agent" / "agent.db"
    if omp_db.exists():
        try:
            conn = sqlite3.connect(str(omp_db), timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT provider, data FROM auth_credentials WHERE disabled_cause IS NULL")
            for provider, raw_data in cur.fetchall():
                if provider not in creds and raw_data:
                    try:
                        creds[provider] = json.loads(raw_data)
                    except Exception:
                        continue
            conn.close()
        except Exception:
            pass

    return creds


# -----------------------------------------------------------------------------
# 2. Direct OpenAI Codex Wire Provider (ChatGPT Subscription)
# -----------------------------------------------------------------------------

@dataclass
class CodexWireProvider:
    """Direct HTTPS wire caller for ChatGPT's Codex backend."""
    access_token: str
    account_id: str
    model: str = "gpt-5.6-sol"
    timeout: float = 60.0

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = "https://chatgpt.com/backend-api/codex/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "chatgpt-account-id": str(self.account_id),
            "OpenAI-Beta": "responses=true",
            "originator": "codex",
            "version": "0.144.1",
            "User-Agent": "gorkbot/0.0.1",
            "accept": "text/event-stream",
        }

        # Convert messages into Codex input items
        input_items: list[dict[str, Any]] = []
        for msg in effect.messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if not content:
                continue
            codex_role = "user" if role in ("user", "system") else "assistant"
            prefix = f"[{role.upper()}]\n" if role == "system" else ""
            input_items.append({
                "role": codex_role,
                "content": [{"type": "input_text", "text": f"{prefix}{content}"}],
            })

        if not input_items:
            input_items.append({"role": "user", "content": [{"type": "input_text", "text": "Hello"}]})

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "store": False,
            "stream": True,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        collected_text: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                        etype = event.get("type")
                        if etype == "response.output_text.delta":
                            collected_text.append(event.get("delta", ""))
                        elif etype == "response.done":
                            res = event.get("response", {})
                            usage_raw = res.get("usage", {})
                            if usage_raw:
                                prompt_tokens = usage_raw.get("input_tokens", 0)
                                completion_tokens = usage_raw.get("output_tokens", 0)
                    except Exception:
                        continue

            output_text = "".join(collected_text).strip()
            return ModelCompleted(
                content=output_text,
                tool_calls=[],
                usage={
                    "prompt_tokens": prompt_tokens or (sum(len(m.get("content", "")) for m in effect.messages) // 4),
                    "completion_tokens": completion_tokens or (len(output_text) // 4),
                },
                finish_reason="stop",
                seat_id=f"wire:codex:{self.model}",
            )

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (401, 403, 429, 500, 502, 503)
            return ModelFailed(
                error=f"Codex wire HTTP {e.code}: {err_body}",
                seat_id=f"wire:codex:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Codex wire request failed: {str(e)}",
                seat_id=f"wire:codex:{self.model}",
                retryable=True,
            )


# -----------------------------------------------------------------------------
# 3. Direct xAI Grok Wire Provider (SuperGrok Subscription)
# -----------------------------------------------------------------------------

@dataclass
class GrokWireProvider:
    """Direct HTTPS wire caller for xAI Grok using OAuth subscription tokens."""
    access_token: str
    model: str = "grok-4.5"
    timeout: float = 60.0

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "gorkbot/0.0.1",
        }

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
                res = json.loads(resp.read().decode("utf-8"))
                choice = res.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content")
                tool_calls = message.get("tool_calls", [])
                usage = res.get("usage", {})

                return ModelCompleted(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason=choice.get("finish_reason", "stop"),
                    seat_id=f"wire:grok:{self.model}",
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (401, 403, 429, 500, 502, 503)
            return ModelFailed(
                error=f"Grok wire HTTP {e.code}: {err_body}",
                seat_id=f"wire:grok:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Grok wire request failed: {str(e)}",
                seat_id=f"wire:grok:{self.model}",
                retryable=True,
            )


# -----------------------------------------------------------------------------
# 4. Fallback Model Provider (Seamless Wire -> Harness Protection)
# -----------------------------------------------------------------------------

@dataclass
class FallbackModelProvider:
    """Tries a high-speed primary wire provider, automatically falling back to a robust harness."""
    primary: ModelProvider
    fallback: ModelProvider
    name: str = "wire_with_fallback"

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        try:
            result = self.primary.call(effect)
            if isinstance(result, ModelCompleted):
                return result
            # Primary failed, attempt fallback
            print(f"\033[1;33m[Seam Fallback]\033[0m Primary '{getattr(self.primary, 'model', 'wire')}' failed: {result.error}. Shifting to fallback harness...")
        except Exception as e:
            print(f"\033[1;33m[Seam Fallback]\033[0m Primary exception: {e}. Shifting to fallback harness...")

        return self.fallback.call(effect)


# -----------------------------------------------------------------------------
# 5. Master Factory for Wire Providers with Fallback
# -----------------------------------------------------------------------------

def create_wire_model_provider(seat: Any) -> ModelProvider:
    """Create a high-speed Wire Provider with automatic CLI harness fallback."""
    provider = getattr(seat, "provider", "").lower()
    model = getattr(seat, "model", "")
    api_key = getattr(seat, "api_key", None)
    endpoint = getattr(seat, "endpoint", "")

    creds = load_local_oauth_credentials()

    if provider == "codex-wire":
        codex_data = creds.get("openai-codex", {})
        token = codex_data.get("access")
        account_id = codex_data.get("accountId") or codex_data.get("account_id")
        if token and account_id:
            wire = CodexWireProvider(access_token=token, account_id=str(account_id), model=model or "gpt-5.6-sol")
            cli = CLIModelProvider(harness="codex", model=model or "gpt-5.6-sol")
            return FallbackModelProvider(primary=wire, fallback=cli, name="codex-wire+cli")
        return CLIModelProvider(harness="codex", model=model or "gpt-5.6-sol")

    elif provider == "grok-wire":
        xai_data = creds.get("xai-oauth", {})
        token = xai_data.get("access")
        if token:
            wire = GrokWireProvider(access_token=token, model=model or "grok-4.5")
            return wire

    elif provider == "gemini":
        return GeminiModelProvider(api_key=api_key, model=model or "gemini-3.6-flash")

    elif provider in ("codex", "claude", "omp", "cli"):
        return CLIModelProvider(harness=provider, model=model or "default")

    return OpenAIModelProvider(
        api_key=api_key,
        base_url=endpoint or "https://api.openai.com/v1",
        model=model or "gpt-4o",
    )
